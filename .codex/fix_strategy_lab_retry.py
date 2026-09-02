from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one target, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "hybrid_runtime/strategy_lab_bridge.py",
    '''    elif checkpoint_status == "failed":
        result["status"] = "failed"
        result["stage"] = "failed"
        result["last_error"] = message or "Strategy Lab cloud execution failed"
    elif checkpoint_status == "running" and current_status in {"queued", "pending", "retry"}:
''',
    '''    elif checkpoint_status == "failed":
        # A checkpoint describes one execution attempt, not the durable queue's
        # final decision. The main queue may already have moved this job to
        # `retry`, so never terminalize the desktop from checkpoint failure alone.
        payload["strategy_lab_checkpoint_status"] = "failed"
        if current_status in {"queued", "pending", "retry", "retry_wait"}:
            result["stage"] = "cloud_queued"
            payload["distributed_stage"] = "cloud_queued"
            payload["distributed_message"] = (
                message or "Strategy Lab attempt ended; waiting for durable cloud retry."
            )
        result["payload"] = payload
    elif checkpoint_status == "running" and current_status in {"queued", "pending", "retry"}:
''',
)

replace_once(
    "test_strategy_lab_cloud_bridge.py",
    '''    STRATEGY_LAB_CHECKPOINT_PATH,
    prepare_strategy_lab_publication,
)''',
    '''    STRATEGY_LAB_CHECKPOINT_PATH,
    overlay_strategy_lab_checkpoint,
    prepare_strategy_lab_publication,
)''',
)

file = ROOT / "test_strategy_lab_cloud_bridge.py"
text = file.read_text(encoding="utf-8")
marker = "def test_failed_attempt_checkpoint_does_not_terminalize_durable_retry():"
if marker not in text:
    text += '''\n\n
def test_failed_attempt_checkpoint_does_not_terminalize_durable_retry():
    remote = {
        "id": "remote-1",
        "type": "strategy_lab",
        "status": "retry",
        "stage": "strategy_lab_execution_retry",
        "progress": 0.63,
        "payload": {"run_id": "strategy-lab-test-1"},
    }
    checkpoint = {
        "id": "strategy-lab-test-1",
        "record_type": "strategy_lab_checkpoint",
        "status": "failed",
        "progress": 0.63,
        "stage": "failed",
        "message": "Attempt 1 stopped; retry is queued.",
    }
    effective = overlay_strategy_lab_checkpoint(remote, checkpoint)
    assert effective["status"] == "retry"
    assert effective["stage"] == "cloud_queued"
    assert effective["payload"]["strategy_lab_checkpoint_status"] == "failed"
    assert "retry" in effective["payload"]["distributed_message"].lower()
'''
    file.write_text(text, encoding="utf-8")

print("Strategy Lab retry reconciliation fix applied")
