import json
from types import SimpleNamespace

import pytest

from geoagent.report.question_library import (
    add_vector_candidates,
    load_question_library,
    match_parsed_questions,
    match_question,
)


def _library(tmp_path):
    path = tmp_path / 'questions.json'
    path.write_text(json.dumps({
        'name': 'test',
        'entries': [
            {
                'id': 'total_summary',
                'question': '全库共有多少个变化图斑、总面积是多少',
                'aliases': ['一共有多少图斑'],
                'keywords': ['总数', '总面积', '全库'],
                'intent': '全库汇总',
                'strategy': 'sql',
                'sql': 'SELECT 1',
                'unit': '平方米',
            },
            {
                'id': 'by_county',
                'question': '分区县统计变化图斑数量和总面积',
                'aliases': ['分行政区统计'],
                'keywords': ['区县', '行政区', '面积'],
                'intent': '分县汇总',
                'strategy': 'sql',
                'sql': 'SELECT 1',
                'unit': '平方米',
            },
        ],
    }, ensure_ascii=False), encoding='utf-8')
    return load_question_library(path)


def test_match_returns_explainable_candidates(tmp_path) -> None:
    result = match_question('请查询全库图斑总数和总面积', _library(tmp_path))
    assert result['matched_question_id'] == 'total_summary'
    assert result['confidence'] > 0
    assert result['match_reasons']


def test_match_keeps_human_confirmation_state(tmp_path) -> None:
    questions = [{'id': 'Q1', 'question': '请分行政区统计变化图斑面积', 'status': '待用户确认'}]
    result = match_parsed_questions(questions, _library(tmp_path))
    assert result[0]['question_library_match']['matched_question_id'] == 'by_county'
    assert result[0]['status'] == '待用户确认'


def test_illegal_land_question_does_not_fall_back_to_generic_county(tmp_path) -> None:
    result = match_question('按县统计疑似新增违法建设用地面积', _library(tmp_path))
    assert result['status'] == 'unmatched'
    assert result['candidates']
    assert all(not candidate['compatible'] for candidate in result['candidates'])


def test_unmatched_returns_three_ranked_candidates(tmp_path) -> None:
    library = _library(tmp_path)
    library['entries'].append({**library['entries'][0], 'id': 'third', 'question': '统计道路长度'})
    result = match_question('统计河流变化比例', library)
    assert result['status'] == 'unmatched'
    assert len(result['candidates']) == 3
    assert all(candidate['match_reasons'] for candidate in result['candidates'])


@pytest.mark.asyncio
async def test_vector_only_adds_candidates_without_approving_match(tmp_path) -> None:
    library = _library(tmp_path)
    question = {'id': 'Q1', 'question': '统计河流变化比例'}
    matched = match_parsed_questions([question], library)
    original_status = matched[0]['question_library_match']['status']

    class FakeStore:
        async def count(self, kind):
            assert kind == 'question'
            return 2

    class FakeKnowledge:
        embedder_available = True
        store = FakeStore()

        async def search_questions(self, query, top_k):
            assert query == question['question']
            assert top_k == 3
            return [SimpleNamespace(id='by_county')]

    enriched = await add_vector_candidates(matched, library, FakeKnowledge())
    result = enriched[0]['question_library_match']
    assert result['status'] == original_status
    assert result['candidates'][0]['id'] == 'by_county'
    assert result['candidates'][0]['retrieval_source'] == 'vector'


@pytest.mark.asyncio
async def test_no_vector_index_keeps_rule_candidates(tmp_path) -> None:
    matched = match_parsed_questions([{'question': '统计河流变化比例'}], _library(tmp_path))

    class EmptyStore:
        async def count(self, kind):
            return 0

    knowledge = SimpleNamespace(embedder_available=True, store=EmptyStore())
    before = matched[0]['question_library_match']['candidates'].copy()
    await add_vector_candidates(matched, _library(tmp_path), knowledge)
    assert matched[0]['question_library_match']['candidates'] == before
