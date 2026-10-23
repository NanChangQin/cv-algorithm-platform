import json
from importlib.resources import files

import pytest

from cv_algorithm_sdk import AlgorithmManifest
from cv_algorithm_sdk.parameters import validate_value
from cv_algorithm_sdk.runtime_contract import (
    TRAINING_DEFAULTS,
    get_contract,
    normalize_parameters,
    prediction_parameters,
    validate_placement,
)


def test_catalog_accounts_for_pinned_defaults_and_validates_all_modes():
    catalog = json.loads(files('cv_algorithm_sdk').joinpath('ultralytics-8.4.183.json').read_text(encoding='utf-8'))
    assert catalog['default_key_count'] == 113
    covered = set()
    for mode in ('train', 'val', 'predict', 'export', 'track', 'benchmark', 'tune'):
        contract = get_contract(mode)
        assert contract == get_contract(mode)
        for name, spec in contract['parameters'].items():
            covered.add(name)
            validate_value(name, spec['default'], spec['json_schema'])
            assert spec['title'] and spec['description']
            if spec['status'] != 'configurable':
                assert spec['reason']
    assert covered == set(catalog['parameters'])


def test_training_keeps_defaults_and_complex_types():
    assert normalize_parameters('train', {}) == TRAINING_DEFAULTS
    values = {'batch': .6, 'freeze': [0, 'model.2'], 'classes': [0, 2],
              'amp': 'bf16', 'fraction': [.8, .5, 0], 'scale': [.5, 1.5], 'time': None}
    result = normalize_parameters('train', values)
    assert result == {**TRAINING_DEFAULTS, **values}
    result['freeze'].append(9)
    assert values['freeze'] == [0, 'model.2']


@pytest.mark.parametrize('values', [
    {'epohs': 10}, {'device': '0'}, {'resume': True}, {'data': '/etc/passwd'},
    {'split': 'test'}, {'batch': 0}, {'batch': True}, {'epochs': '10'}, {'imgsz': 650}, {'imgsz': [640, 640]},
    {'classes': [True]}, {'freeze': [None]}, {'amp': 'int8'}, {'scale': [2, 1]},
    {'lr0': float('nan')}, {'pretrained': '/arbitrary.pt'}, {'fraction': [1, 1, -1]},
])
def test_training_rejects_unknown_managed_and_malformed_values(values):
    with pytest.raises(ValueError):
        normalize_parameters('train', values)


def test_prediction_aliases_and_precisions_are_validated():
    result = normalize_parameters('predict', {
        'confidence': 0, 'image_size': [640, 960], 'classes': None, 'half': True,
    })
    assert result == {'conf': 0, 'imgsz': [640, 960], 'classes': None, 'quantize': 16}
    for values in ({'confidence': .3, 'conf': .4}, {'int8': True}, {'half': 1}):
        with pytest.raises(ValueError):
            normalize_parameters('predict', values)
    specs = prediction_parameters()
    assert len(specs) == 12
    assert specs['iou_threshold']['default'] == .45


def test_placement_rejects_autobatch_rect_and_nondivisible_ddp():
    for parameters in ({'batch': -1}, {'batch': .8}, {'batch': 3}, {'batch': 8, 'rect': True}):
        with pytest.raises(ValueError):
            validate_placement(parameters, 2)
    validate_placement({'batch': 8}, 2)


def test_integer_json_numbers_are_canonicalized_without_changing_fraction_semantics():
    result = normalize_parameters('train', {'epochs': 3.0, 'batch': 8.0,
                                            'classes': [0.0], 'fraction': 1.0})
    assert type(result['epochs']) is int
    assert type(result['batch']) is int
    assert type(result['classes'][0]) is int
    assert type(result['fraction']) is float


@pytest.mark.parametrize('values', [
    {'half': True, 'quantize': 32}, {'int8': True, 'half': True},
    {'end2end': True, 'nms': True}, {'half': True, 'quantize': []},
])
def test_deprecated_alias_conflicts_are_not_silently_ignored(values):
    with pytest.raises(ValueError):
        normalize_parameters('predict', values)


def manifest(version='1.1', schema=None):
    return AlgorithmManifest.model_validate({
        'schema_version': version, 'id': 'json-detector', 'name': 'JSON', 'version': '1.0.0',
        'task_type': 'object_detection', 'runtime': {'framework': 'pytorch'},
        'input': {'media_types': ['image/jpeg']}, 'output': {'type': 'object_detection'},
        'parameters': {'settings': {'type': 'json', 'default': {'nested': [None, False, 0]},
                                    'json_schema': schema or {'type': 'object'}}},
    })


def test_manifest_json_roundtrip_and_isolated_defaults():
    model = manifest()
    resolved = model.resolve_parameters({})
    assert resolved == {'settings': {'nested': [None, False, 0]}}
    resolved['settings']['nested'].append('mutated')
    assert model.resolve_parameters({})['settings']['nested'] == [None, False, 0]
    assert AlgorithmManifest.model_validate_json(model.model_dump_json()) == model
    with pytest.raises(ValueError):
        model.resolve_parameters({'unknown': 1})
    with pytest.raises(ValueError):
        model.resolve_parameters({'settings': {'bad': float('inf')}})


@pytest.mark.parametrize('version,schema', [
    ('1.0', None), ('1.1', {'$ref': 'https://example.org/schema'}),
    ('1.1', {'type': 'not-a-type'}),
])
def test_manifest_rejects_invalid_or_incompatible_schema(version, schema):
    with pytest.raises(ValueError):
        manifest(version, schema)


def test_exported_contract_fixture_and_training_image_marker_stay_in_sync():
    from pathlib import Path
    from cv_algorithm_sdk.runtime_contract import CONTRACT_ID

    root = Path(__file__).parents[3]
    fixture = json.loads((root / 'docs/research/training-parameter-contract.json').read_text('utf-8'))
    assert fixture == get_contract('train')
    assert f'LABEL cv.platform.parameter-contract="{CONTRACT_ID}"' in (
        root / 'runtimes/yolo-trainer/Dockerfile'
    ).read_text('utf-8')
