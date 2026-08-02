# combo_test.py

import os
import json
import sys as system
from typing import List, Optional
from collections import OrderedDict


class OrderService:
    def create_order(
        self,
        customer_name,
        customer_email,
        shipping_address,
        billing_address,
        payment_method,
        coupon_code,
    ):
        data = OrderedDict()
        data["customer"] = customer_name
        items: List[str] = []
        return json.dumps({"customer": customer_name, "items": items})