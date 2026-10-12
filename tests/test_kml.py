from io import BytesIO
import json
import os

import pytest

from conftest import TEST_EMAIL


KML_NAMESPACE = 'http://www.opengis.net/kml/2.2'
VALID_OUTER = '47,-18,0 48,-18,0 48,-17,0 47,-17,0 47,-18,0'
VALID_HOLE = '47.2,-17.8,0 47.8,-17.8,0 47.8,-17.2,0 47.2,-17.2,0 47.2,-17.8,0'
VALID_OUTER_2 = '49,-18,0 50,-18,0 50,-17,0 49,-17,0 49,-18,0'


def polygon(outer=VALID_OUTER, holes=()):
    inner_boundaries = ''.join(
        '<innerBoundaryIs><LinearRing><coordinates>'
        f'{ring}'
        '</coordinates></LinearRing></innerBoundaryIs>'
        for ring in holes
    )
    return (
        '<Polygon><outerBoundaryIs><LinearRing><coordinates>'
        f'{outer}'
        '</coordinates></LinearRing></outerBoundaryIs>'
        f'{inner_boundaries}</Polygon>'
    )


def kml_document(*contents):
    return (
        f'<kml xmlns="{KML_NAMESPACE}"><Document>'
        f'{"".join(contents)}'
        '</Document></kml>'
    )


def post_kml(client, bearer_headers, content, filename='parcel.kml', extra_fields=None):
    if isinstance(content, str):
        content = content.encode('utf-8')
    form = {
        'owner': 'Client supplied owner label',
        'owner_uuid': 'attacker@example.test',
        'parcel_name': 'Test parcel',
        'location_name': 'Test location',
        'description': 'Test description',
        'price': '',
        'price_m2': '',
        'currency': '',
        'sell_category': 'to_sell',
        'status_category': 'titre',
    }
    if extra_fields:
        form.update(extra_fields)
    form['kmlFile'] = (BytesIO(content), filename)
    return client.post(
        '/api/upload-kml',
        data=form,
        headers=bearer_headers(),
    )


def track_temp_paths(app_module, monkeypatch):
    original = app_module.tempfile.NamedTemporaryFile
    paths = []

    def tracked_tempfile(*args, **kwargs):
        temp_file = original(*args, **kwargs)
        paths.append(temp_file.name)
        return temp_file

    monkeypatch.setattr(app_module.tempfile, 'NamedTemporaryFile', tracked_tempfile)
    return paths, original


def fail_temp_file_write(app_module, monkeypatch):
    original = app_module.tempfile.NamedTemporaryFile
    paths = []

    class FailingWriteFile:
        def __init__(self, *args, **kwargs):
            self.file = original(*args, **kwargs)
            self.name = self.file.name
            paths.append(self.name)

        def __enter__(self):
            self.file.__enter__()
            return self

        def write(self, _content):
            raise OSError('simulated temporary file write failure')

        def __exit__(self, *args):
            return self.file.__exit__(*args)

    monkeypatch.setattr(
        app_module.tempfile,
        'NamedTemporaryFile',
        lambda *args, **kwargs: FailingWriteFile(*args, **kwargs),
    )
    return paths


def test_valid_polygon_upload_preserves_holes_and_ignores_forged_owner_uuid(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response('main_parcels', 'insert', [{'id': 42}])
    response = post_kml(
        client,
        bearer_headers,
        kml_document(polygon(holes=[VALID_HOLE])),
    )

    assert response.status_code == 200
    assert response.get_json()['data']['geometry_type'] == 'Polygon'
    insert_call = next(
        call for call in fake_supabase.calls
        if call['table'] == 'main_parcels' and call['operation'] == 'insert'
    )
    stored = insert_call['payload']
    geometry = json.loads(stored['geojson'])
    assert geometry['type'] == 'Polygon'
    assert len(geometry['coordinates']) == 2
    assert stored['owner'] == 'Client supplied owner label'
    assert stored['owner_uuid'] == TEST_EMAIL


def test_multiple_valid_polygons_are_stored_as_multipolygon(
    client, fake_supabase, bearer_headers
):
    fake_supabase.queue_response('main_parcels', 'insert', [{'id': 43}])
    document = kml_document(
        f'<Placemark>{polygon()}</Placemark>',
        f'<Placemark>{polygon(VALID_OUTER_2)}</Placemark>',
    )

    response = post_kml(client, bearer_headers, document)

    assert response.status_code == 200
    assert response.get_json()['data']['geometry_type'] == 'MultiPolygon'
    insert_call = next(
        call for call in fake_supabase.calls
        if call['table'] == 'main_parcels' and call['operation'] == 'insert'
    )
    geometry = json.loads(insert_call['payload']['geojson'])
    assert geometry['type'] == 'MultiPolygon'
    assert len(geometry['coordinates']) == 2


@pytest.mark.parametrize(
    ('filename', 'content'),
    [
        ('parcel.txt', kml_document(polygon())),
        ('parcel.kml', '<kml><Document>'),
        (
            'parcel.kml',
            kml_document(polygon()).replace(KML_NAMESPACE, 'https://example.test/not-kml'),
        ),
        ('parcel.kml', kml_document('<Placemark><name>No geometry</name></Placemark>')),
        ('parcel.kml', kml_document('<Point><coordinates>47,-18,0</coordinates></Point>')),
        ('parcel.kml', kml_document('<LineString><coordinates>47,-18 48,-18</coordinates></LineString>')),
        ('parcel.kml', kml_document(polygon('181,-18 48,-18 48,-17 47,-17 181,-18'))),
        ('parcel.kml', kml_document(polygon('47,91 48,-18 48,-17 47,-17 47,91'))),
        ('parcel.kml', kml_document(polygon('47,north 48,-18 48,-17 47,-17 47,north'))),
        ('parcel.kml', kml_document(polygon('47,-18 48,-18 48,-17 47,-17 47,-18,NaN'))),
        ('parcel.kml', kml_document(polygon('47,-18 47,-18 47,-18 47,-18'))),
        ('parcel.kml', kml_document(polygon('47,-18 48,-17 48,-18 47,-17 47,-18'))),
        ('parcel.kml', kml_document(polygon('47,-18 48,-18 48,-17 47,-17'))),
        (
            'parcel.kml',
            kml_document(
                '<MultiGeometry>'
                f'{polygon()}'
                '<Point><coordinates>48,-18,0</coordinates></Point>'
                '</MultiGeometry>'
            ),
        ),
    ],
    ids=[
        'wrong-extension',
        'invalid-xml',
        'invalid-kml-namespace',
        'no-geometry',
        'point',
        'linestring',
        'longitude-out-of-range',
        'latitude-out-of-range',
        'non-numeric-coordinate',
        'non-finite-altitude',
        'degenerate-ring',
        'self-intersecting-ring',
        'unclosed-ring',
        'mixed-multigeometry',
    ],
)
def test_invalid_kml_uploads_return_400(
    filename, content, client, bearer_headers
):
    response = post_kml(client, bearer_headers, content, filename=filename)

    assert response.status_code == 400
    if filename == 'parcel.txt':
        assert response.get_json()['message'] == 'Invalid file type. Only KML files are allowed.'
    else:
        assert response.get_json()['message'] == 'Invalid KML file or geometry'


def test_empty_kml_file_returns_400(client, bearer_headers):
    response = post_kml(client, bearer_headers, b'')

    assert response.status_code == 400
    assert response.get_json()['message'] == 'Invalid KML file or geometry'


def test_kml_file_over_10_mib_returns_413(client, bearer_headers, app_module):
    content = b' ' * (app_module.MAX_FILE_SIZE + 1)

    response = post_kml(client, bearer_headers, content)

    assert response.status_code == 413
    assert response.is_json
    assert '10 mib' in response.get_json()['message'].lower()


def test_kml_file_at_10_mib_is_accepted(
    client, fake_supabase, bearer_headers, app_module
):
    fake_supabase.queue_response('main_parcels', 'insert', [{'id': 44}])
    document = kml_document(polygon()).encode('utf-8')
    content = document + (b' ' * (app_module.MAX_FILE_SIZE - len(document)))

    response = post_kml(client, bearer_headers, content)

    assert response.status_code == 200
    assert response.get_json()['data']['geometry_type'] == 'Polygon'


def test_multipart_body_limit_returns_json_413(
    client, bearer_headers, app_module
):
    assert app_module.app.config['MAX_CONTENT_LENGTH'] == (
        app_module.MAX_FILE_SIZE + app_module.MAX_MULTIPART_OVERHEAD
    )
    content = b'x' * (app_module.app.config['MAX_CONTENT_LENGTH'] + 1)

    response = post_kml(client, bearer_headers, content)

    assert response.status_code == 413
    assert response.is_json
    assert response.get_json()['status'] == 'error'


@pytest.mark.parametrize('failure_stage', ['parse', 'insert', 'write'])
def test_temporary_kml_file_is_removed_after_errors(
    failure_stage, client, fake_supabase, bearer_headers, app_module, monkeypatch
):
    paths, _original_tempfile = track_temp_paths(app_module, monkeypatch)
    content = kml_document(polygon())

    if failure_stage == 'parse':
        content = '<kml><Document>'
    elif failure_stage == 'insert':
        original_table = fake_supabase.table

        class FailingInsert:
            def insert(self, _payload):
                return self

            def execute(self):
                raise RuntimeError('simulated database failure')

        def table_with_failing_insert(table_name):
            if table_name == 'main_parcels':
                return FailingInsert()
            return original_table(table_name)

        monkeypatch.setattr(fake_supabase, 'table', table_with_failing_insert)
    else:
        paths[:] = fail_temp_file_write(app_module, monkeypatch)

    response = post_kml(client, bearer_headers, content)

    assert response.status_code == (400 if failure_stage == 'parse' else 500)
    assert paths
    assert all(not os.path.exists(path) for path in paths)
