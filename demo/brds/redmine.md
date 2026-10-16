# Redmine Project Tracker: Business Requirements Document

## 1. Introduction

### 1.1 Purpose
This document describes the business requirements for the Redmine project tracker used by a software team to plan work, track issues and administer the system.

### 1.2 Scope
In scope: sign-in, projects, issue tracking, saved issue lists, time tracking, the wiki, search and system administration. Out of scope: mobile applications and hosting.

## 2. Users and Roles

| Role | Description | Access |
|---|---|---|
| Administrator | System owner | Everything, including the Administration area |
| Developer | A project member | Projects they belong to: issues, time entries, wiki. No Administration area |

## 3. Functional Requirements

### 3.1 Sign-in and Account

FR-1: A user shall be able to sign in with a login name and a password.

FR-2: A signed-in user shall be able to sign out at any time.

FR-3: A signed-in user shall be able to change their own password by providing the current password and the new password twice.

FR-4: A user shall be able to edit their account details: first name, last name and email address. The email address must be a valid address.

Acceptance criteria for FR-1 to FR-4:
- Wrong credentials show an "Invalid user or password" error.
- A new password that does not match its confirmation is rejected with a message.

### 3.2 Projects

FR-5: An administrator shall be able to create a project by giving it a name, a description and an identifier.

FR-6: A user shall be able to see the list of projects they can access.

FR-7: A project member shall be able to open a project overview that shows the project description and its members.

### 3.3 Issues

FR-8: A project member shall be able to create an issue by choosing a tracker and entering a subject and a description.

FR-9: An issue shall have a status, a priority, an assignee, a start date, a due date and an estimated time.

FR-10: A user shall be able to filter the issue list by status, tracker and assignee.

FR-11: A user shall be able to change the columns shown in the issue list.

FR-12: A user shall be able to save an issue filter as a named query for reuse.

FR-13: A project member shall be able to add a comment to an issue.

BR-1: The subject of an issue is required and cannot be longer than 255 characters.

BR-2: The due date of an issue cannot be earlier than its start date.

### 3.4 Time Tracking

FR-14: A project member shall be able to log time spent on an issue, entering the date, the hours and an activity.

FR-15: A user shall be able to see a report of the time logged, grouped by project and by user.

### 3.5 Wiki and Search

FR-16: A project member shall be able to create and edit wiki pages for a project.

FR-17: A user shall be able to search issues, wiki pages and projects from a search field.

### 3.6 Administration

FR-18: An administrator shall be able to create a user account with a login, first name, last name, email address and password.

FR-19: An administrator shall be able to manage roles and the permissions of each role.

FR-20: An administrator shall be able to define the issue trackers and the workflow of statuses for each of them.

FR-21: An administrator shall be able to add custom fields to issues.

### 3.7 Not covered by the current product

FR-22: A project member shall be able to move issue cards between columns on a Kanban board using drag and drop.

FR-23: The system shall post a message to a team chat channel whenever an issue changes status.

FR-24: A user shall be able to sign in with the company single sign-on provider.

## 4. Non-functional Requirements

The system should be intuitive for new team members.

NFR-1: The issue list shall load within 3 seconds for projects with up to 10,000 issues.
