# HelpDesk Support Tickets: Business Requirements Document

## 1. Introduction

### 1.1 Purpose
HelpDesk lets customers raise support tickets and lets agents and administrators work through them within agreed response times.

### 1.2 Scope
In scope: sign-in and session handling, tickets, comments, help articles, SLA targets, users. Out of scope: live chat and phone integration.

## 2. Users and Roles

| Role | Description | Access |
|---|---|---|
| Customer | A person who needs help | Own tickets only: create, read, comment; help articles |
| Agent | Support staff | All tickets: comment, assign to themselves, change status; help articles |
| Admin | Support lead | Everything an Agent can do, plus SLA targets and the user list |

## 3. Functional Requirements

### 3.1 Sign-in and sessions

FR-1: A user shall sign in with an email address and a password; wrong details show an error.

FR-2: A session shall expire after a period of inactivity and the user shall be returned to the sign-in page.

FR-3: A signed-in user shall be able to sign out.

### 3.2 Tickets

FR-4: Customers shall see only their own tickets; agents and admins shall see all tickets.

FR-5: Users shall filter the ticket list by status (Open, In progress, Resolved, Closed) and search tickets by subject.

FR-6: A customer shall create a ticket with a subject, a category, a priority, a description and an optional attachment.

BR-1: A ticket subject is required and at most 120 characters.

BR-2: A ticket description must be at least 20 characters long.

BR-3: A ticket must have a category (Billing, Technical, Account or Other) and a priority (Low, Normal, High or Urgent).

FR-7: Opening a ticket shall show its status, priority, category, assignee and the whole conversation.

FR-8: Users shall add comments to a ticket they can see; an empty comment is refused.

FR-9: Agents and admins shall change a ticket's status and assign it to themselves.

FR-10: Customers shall rate the support they received once a ticket is closed.

FR-11: Ticket handling should be efficient.

### 3.3 Help articles, SLA and users

FR-12: Every user shall search the help articles by title.

FR-13: Admins shall set the first-response target in hours for each priority.

BR-4: A first-response target must be a whole number of hours from 1 to 240.

FR-14: Admins shall see the list of users with name, email and role.

FR-15: Admins shall invite a new agent by email.

### 3.4 Access

FR-16: Only admins shall see the SLA targets and Users pages; customers shall see New ticket and My tickets.

## 4. Non-functional Requirements

The application should be pleasant to use.

NFR-2: Tickets shall be stored for at least 7 years.
