# sample_test.py

class OrderProcessor:
    def __init__(self):
        self.items = []
        self.total = 0.0
        self.customer_email = ""
        self.customer_name = ""
        self.shipping_address = ""

    def add_item(self, name, price):
        self.items.append((name, price))
        self.total += price

    def remove_item(self, name, price):
        self.items.remove((name, price))
        self.total -= price

    def get_total(self):
        return self.total

    def set_customer_info(self, name, email):
        self.customer_name = name
        self.customer_email = email

    def get_customer_summary(self):
        return f"{self.customer_name} <{self.customer_email}>"

    def set_shipping_address(self, address):
        self.shipping_address = address

    def get_shipping_label(self):
        return f"{self.customer_name}\n{self.shipping_address}"