# orders.py

class OrderProcessor:
    def __init__(self):
        self.processed = []

    def process_order(self, customer, items, coupon_code, shipping_country):
        # Block 1: validate input
        if customer is None:
            raise ValueError("Customer is required")
        if not items:
            raise ValueError("Order must contain at least one item")
        for item in items:
            if item.price < 0:
                raise ValueError("Item price cannot be negative")

        # Block 2: compute total with discount
        subtotal = 0.0
        for item in items:
            subtotal += item.price * item.quantity
        discount = 0.0
        if coupon_code == "SAVE10":
            discount = subtotal * 0.10
        total = subtotal - discount

        # Block 3: compute shipping cost
        shipping_cost = 5.0
        if shipping_country != "US":
            shipping_cost += 15.0
        if subtotal > 100:
            shipping_cost = 0.0
        total_with_shipping = total + shipping_cost

        # Block 4: build and store receipt
        receipt = {
            "customer": customer,
            "items": items,
            "total": total_with_shipping,
        }
        self.processed.append(receipt)
        return receipt