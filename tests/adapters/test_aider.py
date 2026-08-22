import tempfile
from pathlib import Path
from codetalker.adapters.aider import AiderAdapter
from codetalker.schema import ActorRole, BlockType


def test_aider_adapter_fixture():
    sample_aider_md = """# aider chat started at 2026-08-20 14:00:00

#### Add a health check endpoint to main.py

I will add a `/health` endpoint to `main.py`.

<<<<<<< SEARCH
def main():
    pass
=======
def health():
    return {"status": "ok"}

def main():
    pass
>>>>>>> REPLACE
"""
    with tempfile.NamedTemporaryFile("w", suffix=".aider.chat.history.md", delete=False, encoding="utf-8") as f:
        f.write(sample_aider_md)
        tmp_path = f.name

    try:
        adapter = AiderAdapter()
        sessions = adapter.discover_sessions(root_path=tmp_path)
        assert len(sessions) == 1
        sess = sessions[0]
        assert sess.harness == "aider"
        assert "Add a health check" in sess.display_name

        steps = adapter.load_steps(sess)
        assert len(steps) >= 2
        # User step
        user_steps = [s for s in steps if s.actor.role == ActorRole.USER]
        assert len(user_steps) >= 1
        assert "Add a health check" in user_steps[0].blocks[0].text

        # Assistant step with diff block
        assistant_steps = [s for s in steps if s.actor.role == ActorRole.ASSISTANT]
        assert len(assistant_steps) >= 1
        block_types = [b.type for b in assistant_steps[0].blocks]
        assert BlockType.CODE_DIFF in block_types
    finally:
        Path(tmp_path).unlink(missing_ok=True)
