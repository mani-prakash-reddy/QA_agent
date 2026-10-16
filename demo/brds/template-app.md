# Item Manager (Full Stack Template App): Business Requirements

## 1. Overview
Item Manager lets registered users keep a personal list of items. Administrators manage user accounts.

## 2. Roles

| Role | Description |
|---|---|
| User | A registered person who manages their own items |
| Admin | A superuser who also manages all users |

## 3. Requirements

### 3.1 Accounts
REQ-1: A visitor shall be able to sign up with a full name, an email address and a password.
REQ-2: Passwords shall be between 8 and 128 characters long.
REQ-3: A registered user shall be able to log in and shall stay logged in until they log out.
REQ-4: A user who forgot their password shall be able to request a password recovery email.
REQ-5: A user shall be able to change their own password by providing the current password.
REQ-6: A user shall be able to delete their own account; this removes all of their items.

### 3.2 Items
REQ-7: A user shall be able to create an item with a title and an optional description.
REQ-8: The item title is required and may be at most 255 characters long.
REQ-9: A user shall be able to edit and delete their own items.
REQ-10: A user shall see only their own items; an administrator shall see all items.
REQ-11: Users shall be able to share an item with another user by email.

### 3.3 Administration
REQ-12: An administrator shall be able to add, edit and delete users.
REQ-13: Only an administrator shall be able to open the Admin page.

## 4. Non-Functional
The application should be responsive and pleasant to use.
