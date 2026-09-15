from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    with client:
        response = client.get('/api/health')
        assert response.status_code == 200
        assert response.json()['status'] == 'ok'


def test_search_api():
    with client:
        response = client.get('/api/search', params={'q':'Kafka partitions','mode':'hybrid'})
        assert response.status_code == 200
        body = response.json()
        assert body['results']
        assert body['results'][0]['title']


def test_ask_is_grounded():
    with client:
        response = client.post('/api/ask', json={'query':'What is BM25?', 'top_k':3})
        assert response.status_code == 200
        body = response.json()
        assert body['grounded'] is True
        assert body['citations']


def test_admin_route_disabled_without_token():
    with client:
        response = client.post('/api/index/document', json={
            'title':'x',
            'text':'This document contains enough text to satisfy the validation constraints.'
        })
        assert response.status_code == 403
