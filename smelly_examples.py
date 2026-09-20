import os
import sys
import json
import re  # unused import (smell: Unused Imports)


class OrderManager:
    """God Class: handles checkout, billing, email, inventory, PDFs, analytics."""

    def __init__(self):
        self.orders = []
        self.tax_rate = 0.18
        self.discount_rate = 0.1
        self.email_log = []
        self.inventory = {}
        self.analytics_events = []

    def create_order(self, items):
        self.orders.append(items)
        return len(self.orders)

    def calculate_tax(self, total):
        return total * self.tax_rate

    def apply_discount(self, total, is_vip):
        return total * (1 - self.discount_rate) if is_vip else total

    def charge_payment(self, card, amount):
        return f"Charged {amount} to {card}"

    def send_confirmation_email(self, user_email):
        self.email_log.append(user_email)
        return True

    def update_inventory(self, sku, qty):
        self.inventory[sku] = self.inventory.get(sku, 0) - qty

    def generate_invoice_pdf(self, order_id):
        return f"invoice_{order_id}.pdf"

    def log_analytics_event(self, event_name):
        self.analytics_events.append(event_name)

    def process_order(self, order, customer, card):
        # Long Method: validation + tax + discount + charge + inventory + email in one place
        if not order["items"]:
            raise ValueError("empty order")
        if order["total"] < 0:
            raise ValueError("bad total")

        tax = self.calculate_tax(order["total"])

        if customer["is_vip"]:
            order["total"] = self.apply_discount(order["total"], True)

        self.charge_payment(card, order["total"] + tax)

        for item in order["items"]:
            self.update_inventory(item["sku"], item["qty"])

        self.send_confirmation_email(customer["email"])
        self.generate_invoice_pdf(order.get("id", 0))
        self.log_analytics_event("order_completed")

        return order["total"] + tax


class Employee:
    """Data Class: only fields + getters, no real behavior."""

    def __init__(self, name, salary, department):
        self.name = name
        self.salary = salary
        self.department = department

    def get_name(self):
        return self.name

    def get_salary(self):
        return self.salary

    def get_department(self):
        return self.department


class InvoicePrinter:
    """Feature Envy: obsessed with Order's internals instead of its own."""

    def print_invoice(self, order):
        print(order.customer.name)
        print(order.customer.address.street)
        print(order.customer.address.city)
        total = sum(i.price * i.qty for i in order.items)
        print(f"Total: {total}")
        print(order.customer.address.zip_code)


def create_user(first_name, last_name, email, phone, street,
                 city, state, zip_code, country, is_admin, newsletter_opt_in):
    """Long Parameter List: 11 positional args."""
    return {
        "name": f"{first_name} {last_name}",
        "email": email,
        "phone": phone,
        "address": f"{street}, {city}, {state} {zip_code}, {country}",
        "is_admin": is_admin,
        "newsletter_opt_in": newsletter_opt_in,
    }


def calculate_employee_bonus(salary, years):
    """Duplicate Code (1 of 2) — identical logic to calculate_contractor_bonus below."""
    if years > 5:
        bonus = salary * 0.15
    else:
        bonus = salary * 0.05
    tax = bonus * 0.2
    return bonus - tax


def calculate_contractor_bonus(salary, years):
    """Duplicate Code (2 of 2) — identical logic to calculate_employee_bonus above."""
    if years > 5:
        bonus = salary * 0.15
    else:
        bonus = salary * 0.05
    tax = bonus * 0.2
    return bonus - tax