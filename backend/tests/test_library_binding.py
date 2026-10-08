"""非白名单模板通过问题库结果自动回填显式数值字段。"""

from pathlib import Path
import sys

from docx import Document
from fastapi.testclient import TestClient

from geoagent.report.library_binding import bind_library_scalars
from geoagent.server.app import create_app


def test_only_matching_single_numeric_result_is_bound() -> None:
    question = {"slot_ids": ["b1:s0", "b1:s1", "b1:s2"]}
    slots = [
        {"id": "b1:s0", "kind": "placeholder", "text": "{{total_summary.n}}"},
        {"id": "b1:s1", "kind": "placeholder", "text": "{{total_summary.area|mu}}"},
        {"id": "b1:s2", "kind": "placeholder", "text": "{{orig_farmland.n}}"},
    ]
    assert bind_library_scalars(
        question, "total_summary", {"rows": [{"n": 12, "area": 1000}]}, slots,
    ) == {"b1:s0": "12", "b1:s1": "1.50"}
    assert bind_library_scalars(
        question, "total_summary", {"rows": [{"n": 1}, {"n": 2}]}, slots,
    ) == {}
    assert bind_library_scalars(
        question, "total_summary", {"rows": [{"n": "wrong", "area": None}]}, slots,
    ) == {}


class _FakePg:
    async def run_sql(self, sql: str, **_: object) -> dict:
        if '"DLBM" LIKE' in sql:
            return {"columns": ["n", "area_m2"], "rows": [{"n": 4, "area_m2": 2000}]}
        if '"TBLX" IN' in sql:
            return {"columns": ["n", "area_m2"], "rows": [{"n": 3, "area_m2": 1000}]}
        return {"columns": ["n", "area"], "rows": [{"n": 10, "area": 10000}]}

    async def close(self) -> None:
        return None


def test_unlisted_template_queries_and_fills_without_custom_template_mapping(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEOAGENT_REPORTS_DIR", str(tmp_path / "reports"))

    def fake_pdf(_source: Path, output: Path) -> Path:
        output.write_bytes(b"%PDF-1.4\n%%EOF")
        return output

    monkeypatch.setattr("geoagent.server.template_routes.convert_docx_to_pdf", fake_pdf)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from build_unlisted_acceptance_template import build

    source = build(tmp_path / "验收.docx")
    app = create_app()
    app.state.pg = _FakePg()
    with TestClient(app) as client:
        parsed = client.post(
            "/api/report-templates/parse?filename=验收.docx", content=source.read_bytes(),
        )
        assert parsed.status_code == 200
        payload = parsed.json()
        assert payload["supported_template_kind"] is None
        assert len(payload["questions"]) == 11
        assert len(payload["template"]["slots"]) == 19
        assert len(payload["template"]["chart_anchors"]) == 2
        assert sum(q["question_library_match"]["status"] == "unmatched"
                   for q in payload["questions"]) == 2
        workflow_id = payload["workflow_id"]
        matches = {"total_summary": None, "orig_farmland": None, "current_farmland": None}
        for question in payload["questions"]:
            for candidate in question["question_library_match"]["candidates"]:
                if (candidate["id"] in matches and candidate["compatible"]
                    and any(candidate["id"] + "." in slot.get("text", "")
                            for slot in payload["template"]["slots"]
                            if slot["id"] in question["slot_ids"])):
                    matches[candidate["id"]] = question
        assert all(matches.values())
        filled = {}
        for library_id, question in matches.items():
            response = client.post(
                f"/api/report-templates/{workflow_id}/questions/{question['id']}/execute",
                json={"library_id": library_id, "question": question["question"]},
            )
            assert response.status_code == 200, response.text
            filled.update(response.json()["auto_bindings"])
        assert len(filled) == 6
        generated = client.post(
            f"/api/report-templates/{workflow_id}/generate",
            json={"binding_mode": True, "confirmed_slot_ids": list(filled)},
        )
        assert generated.status_code == 200, generated.text
        result = generated.json()
        assert result["review_only"] is True
        assert result["pdf"] is not None
        document = Document(tmp_path / "reports" / result["word"]["filename"])
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert "10个变化图斑" in text
        assert "15.00亩" in text
        assert "4个，面积3.00亩" in text
        assert "3个，面积1.50亩" in text
        assert "待核验亩" in text
