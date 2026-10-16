# Gitea Code Hosting: Business Requirements Document

## 1. Introduction

### 1.1 Purpose
This document describes the business requirements for the self-hosted Gitea service that engineering teams use to store source code, track issues and manage access.

### 1.2 Scope
In scope: sign-in and registration, repositories, issues, organisations, personal settings and site administration. Out of scope: continuous-integration runners and package registries.

## 2. Users and Roles

| Role | Description | Access |
|---|---|---|
| Administrator | Site owner | Everything a User can do, plus Site Administration |
| User | A registered developer | Own repositories, issues, personal settings, organisations they belong to |

## 3. Functional Requirements

### 3.1 Registration and Sign-in

FR-1: A visitor shall be able to register an account by providing a username, an email address, a password and the password again.

FR-2: A registered user shall be able to sign in with a username or email address and a password.

FR-3: A signed-in user shall be able to sign out.

Acceptance criteria for FR-1 to FR-3:
- A password that does not match its confirmation is rejected with a message.
- Wrong credentials show an "Username or password is incorrect" error.

### 3.2 Repositories

FR-4: A signed-in user shall be able to create a repository by entering a name and a description and choosing whether it is private.

FR-5: When creating a repository, the user shall be able to initialise it with a README, a .gitignore template and a license.

FR-6: A visitor shall be able to browse the list of public repositories and search them by name.

FR-7: A repository owner shall be able to change the repository settings, including its description, website and visibility.

BR-1: A repository name is required and may contain only letters, numbers, dashes, underscores and dots.

### 3.3 Issues

FR-8: A user shall be able to create an issue in a repository with a title and a description.

FR-9: A user shall be able to assign labels and a milestone to an issue.

FR-10: A user shall be able to filter the issues of a repository by open and closed state.

FR-11: A user shall be able to comment on an issue.

### 3.4 Organisations

FR-12: A signed-in user shall be able to create an organisation by entering an organisation name and choosing its visibility.

FR-13: An organisation owner shall be able to create teams and add members to them.

### 3.5 Personal Settings

FR-14: A user shall be able to edit their profile: full name, biography and location.

FR-15: A user shall be able to change their password by providing the current password and a new one.

FR-16: A user shall be able to add an SSH key to their account by giving it a title and the public key.

FR-17: A user shall be able to generate an access token with a name to use the API.

### 3.6 Site Administration

FR-18: An administrator shall be able to view the list of all user accounts.

FR-19: An administrator shall be able to create a user account by entering a username, an email address and a password.

FR-20: An administrator shall be able to see all repositories on the site.

### 3.7 Not covered by the current product

FR-21: A user shall be able to design a deployment pipeline in a visual editor.

FR-22: The system shall send a weekly digest email summarising repository activity.

## 4. Non-functional Requirements

The service should feel fast.

NFR-1: The repository list shall load within 2 seconds for sites with up to 5,000 repositories.
