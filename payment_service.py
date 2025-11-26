import time
import uuid
import random

class MockPaymentGateway:
    """
    A service that simulates a real Payment Gateway (like Stripe or Razorpay).
    It is decoupled from our main Flask app.
    """

    @staticmethod
    def process_payment(card_details, amount):
        """
        Simulates processing a payment.
        Returns a dictionary: {'success': Bool, 'transaction_id': Str, 'error': Str}
        """
        
        # 1. Simulate Network Latency
        # Real HTTP requests to banks take 1-3 seconds.
        print(f"Connecting to Bank Server... Processing ${amount}...")
        time.sleep(2) 

        # 2. Input Validation (Basic Security)
        # Real gateways check Luhn algorithms, etc. We'll just check length.
        card_number = card_details.get('number', '')
        if len(str(card_number)) != 16:
            return {
                'success': False,
                'error': 'Invalid Card Number: Must be 16 digits.'
            }

        # 3. Simulate "Business Logic" Failures
        # Sometimes banks reject cards (Insufficient funds, Fraud detection).
        # We'll simulate a random failure 10% of the time.
        if random.random() < 0.1:
            return {
                'success': False,
                'error': 'Transaction Declined: Insufficient Funds.'
            }

        # 4. Success! Generate a Transaction ID
        # This is the "Receipt" or "Reference Number" crucial for tracking.
        transaction_id = f"txn_{uuid.uuid4().hex[:10]}"
        
        return {
            'success': True,
            'transaction_id': transaction_id,
            'error': None
        }