import threading

from database.repository import ScholarshipRepository
from services.demo_loader import load_demo_scholarships
from tests.conftest import ev, make_scholarship


def test_roundtrip_and_evidence_rows(tmp_path):
    repo = ScholarshipRepository(tmp_path / "t.db")
    s = make_scholarship(max_family_income=1, evidence=[ev("max_family_income", "q")])
    repo.upsert(s)
    assert repo.get("s1") == s
    assert repo.count_evidence("s1") == 1
    repo.upsert(s.model_copy(update={"evidence": []}))
    assert repo.count_evidence("s1") == 0  # evidence replaced, not accumulated


def test_demo_records_are_separated(tmp_path):
    repo = ScholarshipRepository(tmp_path / "t.db")
    repo.upsert_many(load_demo_scholarships() + [make_scholarship()])
    assert [s.id for s in repo.list_all()] == ["s1"]
    assert len(repo.list_all(include_demo=True)) == 9


def test_demo_records_all_flagged_and_named():
    demos = load_demo_scholarships()
    assert demos and all(d.is_demo and d.name.startswith("[DEMO]") for d in demos)
    assert all("example.org" in (d.application_url or "") for d in demos)


def test_search_cache_expiry_and_clear(tmp_path):
    repo = ScholarshipRepository(tmp_path / "t.db")
    repo.save_search("k", ["a"], "{}")
    assert repo.get_search("k", 24)[0] == ["a"]
    assert repo.get_search("k", 0) is None
    repo.save_gemini("g", "{}")
    repo.add_history("B.E., Karnataka", 3)
    repo.clear_search_data()
    assert repo.get_search("k", 24) is None and repo.get_gemini("g", 24) is None and repo.list_history() == []


def test_sql_injection_in_ids_is_inert(tmp_path):
    repo = ScholarshipRepository(tmp_path / "t.db")
    repo.upsert(make_scholarship())
    assert repo.get("x'; DROP TABLE scholarships; --") is None
    assert repo.get("s1") is not None


def test_concurrent_writes(tmp_path):
    repo = ScholarshipRepository(tmp_path / "t.db")
    errors = []

    def work(i):
        try:
            for j in range(10):
                repo.upsert(make_scholarship(id=f"{i}-{j}", name=f"n{i}-{j}"))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors and len(repo.list_all()) == 60
