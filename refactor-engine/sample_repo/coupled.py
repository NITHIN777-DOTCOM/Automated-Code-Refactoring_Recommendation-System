"""Two classes with clear high coupling: Order instantiates and drives PaymentProcessor."""


class PaymentProcessor:
    def authorize(self, amount):
        return amount > 0

    def charge(self, amount):
        return amount

    def refund(self, amount):
        return -amount


class Order:
    def __init__(self):
        self.amount = 0
        self.processor = None

    def checkout(self, amount):
        self.amount = amount
        processor = PaymentProcessor()
        if processor.authorize(amount):
            return processor.charge(amount)
        return 0

    def cancel(self):
        processor = PaymentProcessor()
        return processor.refund(self.amount)
