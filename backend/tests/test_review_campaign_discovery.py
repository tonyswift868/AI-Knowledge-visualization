"""Discovery projects the already restored service, never localStorage identity."""
from fastapi.testclient import TestClient
from app.review_product_v1.api import create_review_product_app
from app.review_product_v1.service import ReviewProductService
from app.product_runtime_state import export_review, restore_review


def test_fresh_device_discovers_restored_campaign_without_private_state():
    a = ReviewProductService()
    created = a.create_demo_campaign()
    b = restore_review(export_review(a))
    with TestClient(create_review_product_app(b)) as client:
        items = client.get('/api/review/campaigns').json()['items']
        assert len(items) == 1
        assert items[0]['campaign_id'] == created['campaign_id']
        assert set(items[0]) == {'campaign_id', 'state', 'round'}
        assert client.get('/api/review/campaigns/' + created['campaign_id']).status_code == 200
    with TestClient(create_review_product_app(ReviewProductService())) as other:
        assert other.get('/api/review/campaigns').json() == {'items': []}
        assert other.get('/api/review/campaigns/' + created['campaign_id']).status_code == 404
