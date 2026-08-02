"""Tests for cronjob action='run' immediate execution (#41037).

Before this fix, `cronjob(action='run')` only set next_run_at=now and returned
success, relying on the scheduler ticker to actually run the job. With no
gateway/ticker active (e.g. a CLI-only Windows setup) the job never executed and
last_run_at stayed null forever. Now action='run' claims the job (at-most-once,
blocking a concurrent tick) and fires it inline via the shared run_one_job body.
"""
import json
from unittest.mock import patch

from tools.cronjob_tools import cronjob, _execute_job_now


_JOB = {"id": "job-run-1", "name": "manual run", "prompt": "hi",
        "schedule": {"kind": "cron", "expr": "0 9 * * *"}}


class TestCronjobRunExecutesImmediately:
    def test_run_action_claims_and_fires_via_run_one_job(self):
        """action='run' must claim the job then fire it through run_one_job."""
        ran = {"job": "after-run", "last_status": "ok", "last_error": None}
        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.claim_job_for_fire", return_value=True) as m_claim, \
             patch("cron.scheduler.run_one_job", return_value=True) as m_run, \
             patch("tools.cronjob_tools.get_job", return_value=ran):
            out = json.loads(cronjob(action="run", job_id="job-run-1"))

        assert out["success"] is True
        assert out["job"]["executed"] is True
        assert out["job"]["execution_success"] is True
        m_claim.assert_called_once_with("job-run-1")   # at-most-once claim taken
        m_run.assert_called_once()                       # fired via the shared body


    def test_execute_job_now_bails_without_claim(self):
        """_execute_job_now never calls run_one_job when the claim is lost."""
        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=False), \
             patch("cron.scheduler.run_one_job") as m_run:
            res = _execute_job_now(dict(_JOB))
        assert res["claimed"] is False
        assert res["success"] is False
        m_run.assert_not_called()

    def test_execute_job_now_marks_failure_on_exception(self):
        """An exception during fire is captured, marked failed, not propagated."""
        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.run_one_job", side_effect=RuntimeError("boom")), \
             patch("tools.cronjob_tools.mark_job_run") as m_mark, \
             patch("tools.cronjob_tools.get_job", return_value=dict(_JOB)):
            res = _execute_job_now(dict(_JOB))
        assert res["claimed"] is True
        assert res["success"] is False
        assert "boom" in res["error"]
        m_mark.assert_called_once()

    def test_run_now_polls_activity_heartbeat_while_job_thread_is_alive(self):
        """#76502: a long-running job must not go silent to the watchdog.

        Before this fix, ``run_one_job`` ran directly on the calling
        (parent) thread with no intermediate activity touch, so a
        30+ minute job looked identical to a hung tool call and the
        gateway's inactivity watchdog killed the parent turn even though
        the job was progressing normally. Now the job runs off-thread
        while the calling thread polls ``touch_activity_if_due`` so a
        slow job still keeps the watchdog's heartbeat alive.
        """
        import time as time_mod

        def _slow_run(job):
            time_mod.sleep(0.3)
            return True

        ran = {"last_status": "ok", "last_error": None}
        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.run_one_job", side_effect=_slow_run), \
             patch("tools.cronjob_tools.get_job", return_value=ran), \
             patch("tools.environments.base.touch_activity_if_due") as m_touch:
            res = _execute_job_now(dict(_JOB))

        assert res["claimed"] is True
        assert res["success"] is True
        # The heartbeat loop must have polled at least once while run_one_job
        # was still executing on the background thread — proving a long job
        # keeps producing activity ticks instead of going silent for its
        # entire duration.
        assert m_touch.called
