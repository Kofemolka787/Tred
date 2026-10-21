"""Model-facing references to immutable market facts; no inferred repairs."""
import json
import math
import re


MESSAGES = {
    'decision_json': 'Модель не вернула один полный JSON-объект в требуемом формате.',
    'decision_fields': 'Поля ответа модели не соответствуют формату сценария.',
    'decision_direction': 'Пара, направление или стратегия ИИ отсутствуют среди подтверждённых сценариев.',
    'decision_wait': 'Модель некорректно оформила решение об ожидании.',
    'decision_explanation': 'В ответе модели нет полного объяснения и описания риска.',
    'decision_evidence': 'Для входа нужны как минимум две разные ссылки на подтверждённые данные.',
    'decision_reference': 'Ссылка ИИ не соответствует переданным закрытым свечам или событиям.',
}


class DecisionError(ValueError):
    def __init__(self, code):
        super().__init__(MESSAGES[code] + ' Вход не предложен.')
        self.code = code


WIRE_SCHEMA = {'type': 'object', 'properties': {
    'action': {'type': 'string', 'enum': ['enter', 'wait']},
    'symbol': {'type': 'string'},
    'direction': {'type': 'string', 'enum': ['up', 'down', 'none']},
    'strategy': {'type': 'string'},
    'summary': {'type': 'string'}, 'risk': {'type': 'string'},
    'evidence': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'ref': {'type': 'string'}, 'explanation': {'type': 'string'}},
        'required': ['ref', 'explanation'], 'additionalProperties': False}}},
    'required': ['action', 'symbol', 'direction', 'strategy', 'summary', 'risk', 'evidence'],
    'additionalProperties': False}

FORMAT = """Верни только один JSON-объект, без Markdown, рассуждений и других полей.
Обязательные поля: action (enter или wait), symbol, direction (up/down/none), strategy,
summary (непустое объяснение по-русски), risk (непустое ограничение по-русски), evidence.
Для enter скопируй symbol, direction и strategy из одного элемента qualified. evidence — массив
из 2–4 объектов {"ref":"r0","explanation":"конкретная причина"}. Скопируй ref
ТОЛЬКО из evidence_refs той же пары. Разные ref ссылаются на закрытую свечу или событие;
длинные time и period в ответ не переписывай. Не используй вымышленные ссылки.
Если qualified пуст или ни один сценарий не убедителен, верни wait. Формат ожидания:
{"action":"wait","symbol":"","direction":"none","strategy":"","summary":"Почему входа нет",
"risk":"Чего не хватает или что может отменить сценарий","evidence":[]}.
Это пример формата, а не готовый анализ. Решение принимай только по данным запроса."""


def prepare_payload(snapshot):
    """Refs are rebuilt for each immutable batch and never for unavailable data."""
    refs, seen = [], set()
    for pair in snapshot['pairs']:
        if not pair.get('analysis_ready',pair.get('data_ready')):
            continue
        for frame in pair.get('frames', []):
            period = frame.get('period')
            if type(period) is not int or period not in (60, 120, 300, 600, 900):
                continue
            times = [('closed_bar', frame.get('bar_time'))]
            times += [(e.get('kind', 'event'), e.get('time')) for e in frame.get('events', [])]
            for kind, stamp in times:
                if type(stamp) not in (int, float) or not math.isfinite(stamp):
                    continue
                identity = (pair['symbol'], period, stamp)
                if identity in seen:
                    continue
                seen.add(identity)
                refs.append({'ref': 'r'+str(len(refs)), 'symbol': pair['symbol'],
                             'period': period, 'time': stamp, 'source': kind})
    return {**snapshot, 'evidence_refs': refs}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DecisionError('decision_json')
        result[key] = value
    return result


def _constant(_):
    raise DecisionError('decision_json')


def decode_decision(text, payload):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 65536:
        raise DecisionError('decision_json')
    text = text.strip()
    # A complete, single fence is harmless presentation. Never extract an
    # arbitrary JSON substring, strip reasoning, or salvage truncated output.
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', text, re.I | re.S)
    if fenced:
        text = fenced[1]
    try:
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise DecisionError('decision_json') from None
    if not isinstance(value, dict) or set(value) != set(WIRE_SCHEMA['required']):
        raise DecisionError('decision_fields')
    evidence = value['evidence']
    if not isinstance(evidence, list) or len(evidence) > 12:
        raise DecisionError('decision_evidence')
    refs = {r['ref']: r for r in payload['evidence_refs']}
    mapped = []
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {'ref', 'explanation'}:
            raise DecisionError('decision_fields')
        if not isinstance(item['ref'], str) or item['ref'] not in refs:
            raise DecisionError('decision_reference')
        source = refs[item['ref']]
        mapped.append({k: source[k] for k in ('symbol', 'period', 'time')} |
                      {'explanation': item['explanation']})
    return {**value, 'evidence': mapped}
