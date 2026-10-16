import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.models import Message, User
from app.qa.models import AppRole, Project, RoleCredential
from app.qa.schemas import (
    Credentials,
    ProjectCreate,
    ProjectPublic,
    ProjectUpdate,
    RoleCreate,
    RolePublic,
)
from app.qa.security.crypto import CryptoError, encrypt_json

router = APIRouter(prefix="/projects", tags=["projects"])


def get_owned_project(
    session: SessionDep, user: User, project_id: uuid.UUID
) -> Project:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user.is_superuser and project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return project


def _store_credentials(
    session: SessionDep, role_id: uuid.UUID, creds: Credentials
) -> None:
    payload: dict[str, Any] = {
        "username": creds.username,
        "password": creds.password.get_secret_value(),
    }
    if creds.totp_seed is not None:
        payload["totp_seed"] = creds.totp_seed.get_secret_value()
    if creds.storage_state is not None:
        payload["storage_state"] = creds.storage_state.get_secret_value()
    try:
        ciphertext = encrypt_json(payload)
    except CryptoError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    row = session.exec(
        select(RoleCredential).where(RoleCredential.role_id == role_id)
    ).first()
    if row is None:
        row = RoleCredential(role_id=role_id, ciphertext=ciphertext)
    else:
        row.ciphertext, row.updated_at = ciphertext, datetime.now(UTC)
    session.add(row)


def _role_public(session: SessionDep, role: AppRole) -> RolePublic:
    has = session.exec(
        select(RoleCredential.id).where(RoleCredential.role_id == role.id)
    ).first()
    return RolePublic(
        id=role.id,
        project_id=role.project_id,
        name=role.name,
        description=role.description,
        login_recipe=role.login_recipe,
        has_credentials=has is not None,
    )


@router.post("/", response_model=ProjectPublic)
def create_project(
    session: SessionDep, current_user: CurrentUser, body: ProjectCreate
) -> Any:
    project = Project(
        owner_id=current_user.id,
        name=body.name,
        description=body.description,
        target_base_url=body.target_base_url,
        environment_ack=body.environment_ack,
        confidential=body.confidential,
        crawl_config=body.crawl_config.model_dump(),
        generation_config=body.generation_config.model_dump(),
        budget_caps=body.budget_caps.model_dump(),
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("/", response_model=list[ProjectPublic])
def list_projects(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    stmt = (
        select(Project)
        .order_by(col(Project.created_at).desc())
        .offset(skip)
        .limit(limit)
    )
    if not current_user.is_superuser:
        stmt = stmt.where(Project.owner_id == current_user.id)
    return session.exec(stmt).all()


@router.get("/{project_id}", response_model=ProjectPublic)
def read_project(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    return get_owned_project(session, current_user, project_id)


@router.patch("/{project_id}", response_model=ProjectPublic)
def update_project(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: ProjectUpdate,
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    project.updated_at = datetime.now(UTC)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.delete("/{project_id}", response_model=Message)
def delete_project(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    project = get_owned_project(session, current_user, project_id)
    session.delete(project)
    session.commit()
    return Message(message="Project deleted")


@router.post("/{project_id}/roles", response_model=RolePublic)
def create_role(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    body: RoleCreate,
) -> Any:
    get_owned_project(session, current_user, project_id)
    if session.exec(
        select(AppRole).where(
            AppRole.project_id == project_id, AppRole.name == body.name
        )
    ).first():
        raise HTTPException(
            status_code=409, detail="A role with this name already exists"
        )
    role = AppRole(
        project_id=project_id,
        name=body.name,
        description=body.description,
        login_recipe=body.login_recipe,
    )
    session.add(role)
    session.flush()
    if body.credentials is not None:
        _store_credentials(session, role.id, body.credentials)
    session.commit()
    session.refresh(role)
    return _role_public(session, role)


@router.get("/{project_id}/roles", response_model=list[RolePublic])
def list_roles(
    session: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Any:
    get_owned_project(session, current_user, project_id)
    roles = session.exec(
        select(AppRole)
        .where(AppRole.project_id == project_id)
        .order_by(col(AppRole.name))
    ).all()
    return [_role_public(session, r) for r in roles]


@router.put("/{project_id}/roles/{role_id}/credentials", response_model=RolePublic)
def set_role_credentials(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    role_id: uuid.UUID,
    body: Credentials,
) -> Any:
    get_owned_project(session, current_user, project_id)
    role = session.get(AppRole, role_id)
    if not role or role.project_id != project_id:
        raise HTTPException(status_code=404, detail="Role not found")
    _store_credentials(session, role.id, body)
    session.commit()
    return _role_public(session, role)


@router.delete("/{project_id}/roles/{role_id}", response_model=Message)
def delete_role(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    role_id: uuid.UUID,
) -> Any:
    get_owned_project(session, current_user, project_id)
    role = session.get(AppRole, role_id)
    if not role or role.project_id != project_id:
        raise HTTPException(status_code=404, detail="Role not found")
    session.delete(role)
    session.commit()
    return Message(message="Role deleted")
