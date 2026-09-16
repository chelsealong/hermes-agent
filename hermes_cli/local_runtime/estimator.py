"""Per-layer context-memory estimator + physics check.

The estimator is ADVISORY: fit's allocation is authoritative at launch and the touch generation is
ground truth after it. Unknown shapes round UP (never underestimate memory).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hermes_cli.local_runtime.gguf import GGUFHeader

# q8_0: 34-byte blocks of 32 f16-equivalent elements (exact).
_Q8_BYTES_PER_ELEM = 34 / 32
_F16_BYTES_PER_ELEM = 2.0

# Architectures with a known SWA layer pattern: arch -> fraction of layers that are
# sliding-window. Unknown SWA archs treat every layer as full attention (overestimate; safe).
_SWA_LAYER_FRACTION = {"gemma3": 5 / 6, "gemma2": 1 / 2}

# Per-recurrent-layer state allowance (bytes/seq). Deliberately generous: an entire measured
# hybrid slot state is ~99 MB including 8K tokens of full-attn KV, so tens of MiB total is the
# right order; unknown SSM shapes must never underestimate.
_RECURRENT_STATE_PER_LAYER = 4 << 20


class LayerKind(Enum):
    FULL = "full"
    SWA = "swa"
    RECURRENT = "recurrent"


@dataclass
class ModelProfile:
    """Everything the policy needs, decoupled from GGUF parsing so decision-table tests can
    construct profiles directly."""

    name: str
    weights_bytes: int
    embd_table_bytes: int
    n_ctx_train: int
    layers: list[tuple[LayerKind, int]]   # (kind, kv_bytes_per_token_f16); SWA capped, recurrent ignored
    swa_window: int = 0
    moe: bool = False
    architecture: str = ""
    n_vocab: int = 0            # prices logits buffers (ubatch x vocab)
    # Weights spill_overrides() can never move off the GPU (attention/embedding/KV-critical),
    # i.e. weights_bytes minus whatever a MoE/hybrid spill offloads. 0 means "nothing pinned" —
    # true for dense profiles (whole layers cut back-to-front, attention included) and the default
    # for synthetic profiles built directly rather than through profile_from_gguf.
    pinned_weights_bytes: int = 0
    # Context-cost multiplier. MTP spec decode keeps a small draft context beside the main one;
    # calibrated against four measured server-RSS points on Qwen3.8 Q4 (128K/221K/256K, both
    # postures): the draft adds ~17% to per-token KV; 1.2 rounds up so the error stays on the safe
    # side (+250 MiB at 256K, never negative).
    kv_scale: float = 1.0

    @property
    def per_token_kv_f16(self) -> int:
        """Uncapped per-token KV cost (full + SWA share)."""
        return sum(b for kind, b in self.layers if kind != LayerKind.RECURRENT)

    @property
    def recurrent_layer_count(self) -> int:
        return sum(1 for kind, _ in self.layers if kind == LayerKind.RECURRENT)


@dataclass
class HardwareBudget:
    """Memory the physics check may budget against. Discrete cards may trust the device query;
    unified-memory devices must budget from OS free memory minus headroom (device queries observed
    off by 3x). Callers construct this accordingly; the estimator just consumes it."""

    usable_vram_bytes: int      # live free (discrete) / derived (UMA)
    total_device_bytes: int
    ram_available_bytes: int
    uma: bool = False


def profile_from_gguf(header: GGUFHeader) -> ModelProfile:
    kv_heads = header.head_counts_kv()
    dk, dv = header.head_dim_k, header.head_dim_v
    swa_fraction = _SWA_LAYER_FRACTION.get(header.architecture, 0.0)
    has_swa = header.sliding_window > 0 and swa_fraction > 0

    layers: list[tuple[LayerKind, int]] = []
    n_attn_seen = 0
    n_attn_total = sum(1 for h in kv_heads if h > 0)
    n_swa = round(n_attn_total * swa_fraction) if has_swa else 0
    for heads in kv_heads:
        if heads == 0:
            layers.append((LayerKind.RECURRENT, 0))
            continue
        per_token = round(heads * (dk + dv) * _F16_BYTES_PER_ELEM)
        # Distribute the SWA share across the first n_swa attention layers; only the full/SWA
        # SPLIT matters to the totals, not which indexes.
        kind = LayerKind.SWA if n_attn_seen < n_swa else LayerKind.FULL
        layers.append((kind, per_token))
        n_attn_seen += 1

    moe = header.expert_count > 0
    is_hybrid = any(kind == LayerKind.RECURRENT for kind, _ in layers)
    # Mirrors spill_overrides(): MoE offloads only expert FFN weights, hybrid offloads every FFN
    # weight, dense offloads none via -ot (its spill is llama.cpp's own back-to-front layer cut,
    # which does move attention weights, so nothing is pinned).
    if moe:
        spillable = header.ffn_expert_weight_bytes
    elif is_hybrid:
        spillable = header.ffn_weight_bytes
    else:
        spillable = 0
    pinned = max(0, header.tensor_bytes - spillable)

    return ModelProfile(
        name=header.path, weights_bytes=header.tensor_bytes, embd_table_bytes=header.embd_table_bytes,
        n_ctx_train=header.n_ctx_train, layers=layers, swa_window=header.sliding_window,
        moe=moe, architecture=header.architecture, n_vocab=header.n_vocab,
        pinned_weights_bytes=pinned)


def kv_dtype_factor(flash_attention: bool) -> float:
    """q8_0 with FA (every backend we ship); f16 on exotic non-FA fallbacks — the 64K guarantee
    stands either way, the physics check just prices the doubled KV."""
    return (_Q8_BYTES_PER_ELEM / _F16_BYTES_PER_ELEM) if flash_attention else 1.0


def ctx_bytes(profile: ModelProfile, window: int, *, flash_attention: bool = True) -> int:
    """Context memory for one window: full layers linear in T, SWA layers capped at the sliding
    window, recurrent layers constant. Scaled by profile.kv_scale (MTP draft context)."""
    factor = kv_dtype_factor(flash_attention)
    total = 0.0
    for kind, per_token_f16 in profile.layers:
        if kind == LayerKind.RECURRENT:
            total += _RECURRENT_STATE_PER_LAYER
        elif kind == LayerKind.SWA:
            total += per_token_f16 * factor * min(window, profile.swa_window)
        else:
            total += per_token_f16 * factor * window
    return int(total * profile.kv_scale)


@dataclass
class PhysicsRefusal:
    """The only true refusal: either weights + floor-KV + state exceed VRAM + RAM, or (MoE/hybrid
    only) the weights spill_overrides() can never offload plus floor-KV alone exceed VRAM. The
    remedy is a smaller quant, never a smaller window."""

    needed_bytes: int
    available_bytes: int
    message: str


def footprint_bytes(profile: ModelProfile, window: int, *, flash_attention: bool = True,
                    overhead_bytes: int = 0) -> int:
    """Complete estimated footprint; the hardware budget already excludes its reserve."""
    return (profile.weights_bytes + ctx_bytes(profile, window, flash_attention=flash_attention)
            + max(0, overhead_bytes))


def physics_check(profile: ModelProfile, budget: HardwareBudget,
                  floor: int, *, flash_attention: bool = True,
                  overhead_bytes: int = 0) -> PhysicsRefusal | None:
    window = min(floor, profile.n_ctx_train or floor)
    ctx = ctx_bytes(profile, window, flash_attention=flash_attention)
    overhead = max(0, overhead_bytes)
    gib = 1 << 30

    needed = profile.weights_bytes + ctx + overhead
    available = budget.usable_vram_bytes + budget.ram_available_bytes
    if needed > available:
        return PhysicsRefusal(
            needed_bytes=needed, available_bytes=available,
            message=(f"{profile.name}: needs ~{needed / gib:.1f} GiB at the "
                     f"{floor // 1024}K floor but only ~{available / gib:.1f} GiB "
                     "of VRAM+RAM are available — try a smaller model or a supported smaller quant"))

    # weights+ctx fitting VRAM+RAM is not enough for MoE/hybrid profiles: spill_overrides() only
    # ever offloads FFN/expert weights to host, so pinned_weights_bytes (attention/embedding/
    # KV-critical) plus ctx must fit in VRAM alone — no window or amount of RAM helps otherwise.
    # Dense profiles (pinned_weights_bytes == 0) have no such reserved subset — llama.cpp's own
    # back-to-front layer cut can move attention layers to host too — so they skip this check.
    if profile.pinned_weights_bytes <= 0:
        return None
    pinned_needed = profile.pinned_weights_bytes + ctx + overhead
    if pinned_needed > budget.usable_vram_bytes:
        return PhysicsRefusal(
            needed_bytes=pinned_needed, available_bytes=budget.usable_vram_bytes,
            message=(f"{profile.name}: needs ~{pinned_needed / gib:.1f} GiB at the "
                     f"{floor // 1024}K floor but only ~{budget.usable_vram_bytes / gib:.1f} GiB "
                     "of VRAM are available — try a smaller model or a supported smaller quant"))
    return None
