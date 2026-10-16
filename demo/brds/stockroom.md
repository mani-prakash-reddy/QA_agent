# Stockroom Inventory: Business Requirements Document

## 1. Introduction

### 1.1 Purpose
Stockroom is the internal tool a small warehouse uses to keep products, stock levels, orders and suppliers up to date.

### 1.2 Scope
In scope: sign-in, products, the new-product wizard, orders, suppliers, settings, exports. Out of scope: invoicing and barcode scanners.

## 2. Users and Roles

| Role | Description | Access |
|---|---|---|
| Admin | Warehouse owner | Everything, including suppliers, settings and deleting products |
| Clerk | Warehouse staff | Products and orders; cannot delete products, see suppliers or change settings |
| Viewer | Read-only staff | Can look at products and orders; no screen offers a change |

## 3. Functional Requirements

### 3.1 Sign-in

FR-1: A user shall sign in with an email address and a password; wrong details show an error.

FR-2: A signed-in user shall be able to log out at any time.

### 3.2 Products

FR-3: The dashboard shall show the number of products, how many are low on stock and how many orders are new.

FR-4: Users shall list products with SKU, name, category, price and stock, five per page, and move between pages.

FR-5: Users shall search products by name and filter them by category.

FR-6: Stock at or below the low-stock threshold shall be highlighted in the product list.

FR-7: Opening a product shall show its details, its stock history and its supplier in separate tabs.

FR-8: Admins and clerks shall create a product in three steps: basics, price and stock, review.

BR-1: A product name is required and at most 80 characters.

BR-2: A SKU must look like SKU-1234 and be unique.

BR-3: A price must be a number greater than 0 and stock a whole number from 0 to 100000.

FR-9: Admins and clerks shall edit a product's name, SKU, category, price and stock.

FR-10: Only admins shall delete a product, after a confirmation page that names the product.

FR-11: Products shall be uploaded in bulk from a spreadsheet.

### 3.3 Orders and suppliers

FR-12: Users shall list orders and filter them by status (New, Packed, Shipped).

FR-13: Opening an order shall show the customer, the total and the status.

FR-14: Admins and clerks shall change an order's status.

FR-15: Admins shall list suppliers and add a supplier with a name and a valid contact email.

FR-16: Orders shall be emailed to the customer when they ship.

### 3.4 Settings

FR-17: Admins shall set the low-stock threshold (a whole number from 0 to 1000) and the display currency (EUR, USD or GBP).

### 3.5 Access

FR-18: Viewers shall not be offered any control that changes data.

FR-19: Clerks shall not see the Suppliers and Settings pages.

## 4. Non-functional Requirements

The product list should feel fast.

NFR-2: The product list shall load within 1 second for up to 10,000 products.
