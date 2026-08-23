// @vitest-environment jsdom
// Regression test for #92706: the Keys/Env page fetched `/api/env` once on
// mount and never again, so switching the global profile switcher (sidebar)
// left the *previous* profile's keys on screen instead of the newly
// selected profile's.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/i18n";
import type { EnvVarInfo } from "@/lib/api";

const getEnvVars = vi.fn<() => Promise<Record<string, EnvVarInfo>>>();

vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({
    setAfterTitle: vi.fn(),
    setEnd: vi.fn(),
    setTitle: vi.fn(),
  }),
}));

// Mutable so the test can flip it and simulate the sidebar profile switcher
// (ProfileProvider) changing the globally-selected management profile.
let mockProfile = "";
vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => ({
    profile: mockProfile,
    currentProfile: "default",
    profiles: ["default", "gemini"],
    setProfile: vi.fn(),
  }),
}));

vi.mock("@/plugins", () => ({ PluginSlot: () => null }));

vi.mock("@/lib/api", () => ({
  api: {
    getEnvVars,
    setEnvVar: vi.fn(),
    deleteEnvVar: vi.fn(),
    revealEnvVar: vi.fn(),
    getOAuthProviders: vi.fn().mockResolvedValue([]),
    disconnectOAuthProvider: vi.fn(),
  },
}));

// Imported after the mocks above so EnvPage picks up the mocked modules.
const { default: EnvPage } = await import("./EnvPage");

function envInfo(overrides: Partial<EnvVarInfo>): EnvVarInfo {
  return {
    is_set: false,
    redacted_value: null,
    description: "",
    url: null,
    category: "tool",
    is_password: true,
    tools: [],
    advanced: false,
    ...overrides,
  };
}

// React only routes updates through act() when this flag is set (see
// ChatPage.test.tsx).
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement;
let root: Root;

async function renderPage() {
  await act(async () =>
    root.render(
      <I18nProvider>
        <MemoryRouter>
          <EnvPage />
        </MemoryRouter>
      </I18nProvider>,
    ),
  );
}

beforeEach(() => {
  mockProfile = "";
  getEnvVars.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("EnvPage", () => {
  it("refetches /api/env when the global profile switcher changes", async () => {
    getEnvVars.mockResolvedValueOnce({
      SUPERMEMORY_API_KEY: envInfo({
        is_set: true,
        redacted_value: "sm_Y...KgBb",
      }),
    });
    await renderPage();

    expect(getEnvVars).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("sm_Y...KgBb");

    // Switch the sidebar's global profile scope to "gemini", whose .env has
    // no active SUPERMEMORY_API_KEY.
    getEnvVars.mockResolvedValueOnce({
      SUPERMEMORY_API_KEY: envInfo({ is_set: false, redacted_value: null }),
    });
    mockProfile = "gemini";
    await renderPage();

    expect(getEnvVars).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain("sm_Y...KgBb");
  });
});
