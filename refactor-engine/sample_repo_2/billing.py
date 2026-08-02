"""Billing domain: a value object, an invoice, and a formatter for it."""


class Money:
    def __init__(self, amount, currency="USD"):
        self.amount = amount
        self.currency = currency

    def add(self, other):
        if other.currency != self.currency:
            raise ValueError("Currency mismatch")
        return Money(self.amount + other.amount, self.currency)

    def subtract(self, other):
        if other.currency != self.currency:
            raise ValueError("Currency mismatch")
        return Money(self.amount - other.amount, self.currency)

    def is_negative(self):
        return self.amount < 0

    def __str__(self):
        return f"{self.amount:.2f} {self.currency}"


class Invoice:
    def __init__(self, customer_name):
        self.customer_name = customer_name
        self.billing_street = ""
        self.billing_city = ""
        self.billing_zip = ""
        self.line_items = []
        self.tax_rate = 0.0

    def set_billing_address(self, street, city, zip_code):
        self.billing_street = street
        self.billing_city = city
        self.billing_zip = zip_code

    def formatted_address(self):
        return f"{self.billing_street}, {self.billing_city} {self.billing_zip}"

    def add_line_item(self, description, amount):
        self.line_items.append((description, amount))

    def subtotal(self):
        total = 0.0
        for _description, amount in self.line_items:
            total += amount
        return total

    def total_with_tax(self):
        subtotal = self.subtotal()
        return subtotal + (subtotal * self.tax_rate)

    def set_tax_rate(self, rate):
        self.tax_rate = rate


class InvoiceFormatter:
    def __init__(self):
        self.page_width = 80

    def format_summary(self, invoice):
        lines = [invoice.customer_name, invoice.formatted_address()]
        for description, amount in invoice.line_items:
            lines.append(f"{description}: {amount:.2f}")
        lines.append(f"Subtotal: {invoice.subtotal():.2f}")
        lines.append(f"Total: {invoice.total_with_tax():.2f}")
        return "\n".join(lines)

    def format_header(self, invoice):
        title = f"Invoice for {invoice.customer_name}"
        return title.center(self.page_width)
