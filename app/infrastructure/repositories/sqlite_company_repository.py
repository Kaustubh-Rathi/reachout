"""SQLite / SQLAlchemy implementation of CompanyRepository port."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.company import Company, normalize_company_name
from app.infrastructure.models import CompanyModel
from app.ports.repositories import CompanyRepository


class SqliteCompanyRepository(CompanyRepository):
    """Repository handling persistence and queries for Company entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, company_id: str) -> Optional[Company]:
        model = self.session.get(CompanyModel, company_id)
        return model.to_domain() if model else None

    def get_by_normalized_name(self, normalized_name: str) -> Optional[Company]:
        clean_name = normalize_company_name(normalized_name)
        stmt = select(CompanyModel).where(CompanyModel.normalized_name == clean_name).limit(1)
        model = self.session.scalars(stmt).first()
        return model.to_domain() if model else None

    def list_all(self) -> List[Company]:
        stmt = select(CompanyModel).order_by(CompanyModel.name)
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def save(self, company: Company) -> Company:
        existing = self.session.get(CompanyModel, company.id)
        if existing:
            existing.name = company.name
            existing.normalized_name = company.normalized_name
            existing.domain = company.domain
            existing.updated_at = company.updated_at
        else:
            new_model = CompanyModel.from_domain(company)
            self.session.add(new_model)
        self.session.flush()
        return company
