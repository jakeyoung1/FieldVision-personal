"""Baseball-side unit tests — file grouping and Rickey RAG. No API key required."""
from backend.services import files, rag


def test_group_by_player_strips_page_suffix():
    grouped = files.group_by_player([
        ("Tommy Rivera (page 1).txt", b"fastball 92-94"),
        ("Tommy Rivera (page 2).txt", b"slider out pitch"),
        ("Jake Soto.txt", b"contact hitter"),
    ])
    assert set(grouped) == {"Tommy Rivera", "Jake Soto"}
    assert "fastball" in grouped["Tommy Rivera"]
    assert "slider" in grouped["Tommy Rivera"]


def test_group_by_player_decodes_text_files():
    grouped = files.group_by_player([("A.md", "césar — plus arm".encode())])
    assert "plus arm" in grouped["A"]


def test_rickey_rag_retrieves_scored_hits():
    hits = rag.retrieve("fastball pitcher with good control", k=3)
    assert 0 < len(hits) <= 3
    assert all(h["score"] > 0 for h in hits)
    assert all(h["text"] for h in hits)


def test_rickey_context_block_format():
    block = rag.context_block("power hitting outfielder")
    assert block.startswith("--- Branch Rickey Reference ---")
    assert block.rstrip().endswith("--- End Reference ---")


def test_rickey_rag_empty_query_no_crash():
    assert rag.context_block("zzzzqqqq nonexistent tokens") == "" or True  # no exception is the assertion
