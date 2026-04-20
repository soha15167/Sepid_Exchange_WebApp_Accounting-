from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)

# Test different transfer types with 1,000,000 amount
test_cases = [
    ('کارت به کارت', 1000000),
    ('ساتنا', 1000000),
    ('پایا', 1000000),
]

print("Fee calculations test:")
for transfer_type, amount in test_cases:
    r = c.post('/transactions', json={
        'type': 'ایران',
        'iran_type': 'خروجی',
        'iran_amount': amount,
        'bank_name': 'ملی',
        'transfer_type': transfer_type,
        'jdate': '1405/01/01'
    })
    data = r.json()
    fee = data.get('deposit_fee')
    tax = data.get('tax')
    print(f'{transfer_type}: amount={amount:,}, fee={fee:,.0f}, tax={tax:,.0f}')
