# LeaveTrack Leave Management: Business Requirements Document

## 1. Introduction

### 1.1 Purpose
LeaveTrack lets employees request leave, managers decide on their team's requests, and HR administer employees and leave policies.

### 1.2 Scope
In scope: sign-in, leave requests, approvals, employee records, leave policies, reports. Out of scope: payroll and calendar integrations.

## 2. Users and Roles

| Role | Description | Access |
|---|---|---|
| Employee | Any staff member | Own leave: dashboard, request leave, history |
| Manager | Line manager | Everything an Employee can do, plus decisions on pending requests |
| HR | Human resources | Everything a Manager can do, plus employees, leave policies and reports. Signs in with a second factor |

## 3. Functional Requirements

### 3.1 Sign-in

FR-1: A user shall sign in with an email address and a password.

FR-2: HR users shall also enter a six-digit verification code from an authenticator app when they sign in.

FR-3: A signed-in user shall be able to sign out from the account menu.

FR-4: A user who is not signed in shall be sent to the sign-in page.

### 3.2 Leave requests

FR-5: The dashboard shall show the employee's remaining annual leave days and the number of pending requests.

FR-6: An employee shall request leave by choosing a leave type (Annual, Sick, Unpaid or Parental), a start date, an end date and an optional reason.

FR-7: The employee shall see their leave history with type, dates, days and status.

BR-1: The end date of a request cannot be before its start date.

BR-2: The reason of a request must be at most 200 characters.

BR-3: An annual leave request cannot use more days than the remaining balance; the employee is told how many days are left.

FR-8: Leave requests shall be easy to submit.

### 3.3 Approvals

FR-9: A manager shall see the pending requests and approve or reject each one.

BR-4: Rejecting a request requires a comment of at least five characters.

FR-10: Employees shall be notified by email when their request is decided.

### 3.4 Employees and policies (HR)

FR-11: HR shall see the list of employees with name, email, department and role.

FR-12: HR shall add an employee by entering a full name, a work email, a department, a role and a start date.

BR-5: An employee's name is required and at most 60 characters; the work email must be a valid, unused address.

FR-13: HR shall set the annual leave days, the maximum carry-over days and the minimum notice in days.

BR-6: Annual leave days must be a whole number from 0 to 60, carry-over from 0 to 10, and notice from 0 to 30.

FR-14: HR shall see leave taken by leave type and by department.

FR-15: HR shall export the employee list to a spreadsheet.

### 3.5 Access

FR-16: Only managers and HR shall see the Team requests page; only HR shall see Employees, Leave policies and Reports.

FR-17: A user shall be able to edit their profile picture.

## 4. Non-functional Requirements

The system should be intuitive for first-time users.

NFR-2: Every page shall load within 2 seconds for up to 500 employees.
