from app.services.retrieval import ranking_service as rs


def test_empty_input():
    assert rs.rerank_results("q", []) == []


def test_falls_back_to_vector_order_and_keeps_metadata(monkeypatch):
    def boom(*_):
        raise RuntimeError("model missing")

    monkeypatch.setattr(rs, "_score", boom)
    results = [{"content": f"c{i}", "source": f"s{i}"} for i in range(8)]
    out = rs.rerank_results("q", results, top_n=3)
    assert [r["source"] for r in out] == ["s0", "s1", "s2"]


def test_reorders_and_preserves_source(monkeypatch):
    # pretend the cross-encoder ranks passage 2 first, then 0
    monkeypatch.setattr(rs, "_score", lambda q, p: [{"id": 2, "score": 0.9, "text": "c2"}, {"id": 0, "score": 0.4, "text": "c0"}])
    results = [{"content": f"c{i}", "source": f"s{i}"} for i in range(3)]
    out = rs.rerank_results("q", results, top_n=2)
    assert [r["source"] for r in out] == ["s2", "s0"]
    assert out[0]["rerank_score"] == 0.9
