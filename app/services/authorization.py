"""
AuthorizationService — Phase 23 permission resolution.

Resolution order for effective_mask(user, aircraft, tenant):
  1. Admin bypass → ALL bits
  2. all_planes row for (user, tenant) → use its mask (or role default if NULL)
  3. Per-aircraft row for (user, aircraft) → use its mask (or role default if NULL)
  4. Profile-type default mask for the user's role

The `can(user_id, action, aircraft_id, tenant_id)` wrapper maps the
plain action string to a PermissionBit and calls effective_mask.

view_only flag suppresses all write bits regardless of the resolved mask.
"""

from __future__ import annotations


class AuthorizationService:
    @staticmethod
    def effective_mask(user_id: int, aircraft_id: int | None, tenant_id: int) -> int:
        """Return the resolved PermissionBit mask for user on a specific aircraft.

        Pass aircraft_id=None to get the tenant-level mask (e.g. for list queries).
        """
        from models import (
            PermissionBit,
            Role,
            TenantUser,
            User,
            UserAircraftAccess,
            UserAllAircraftAccess,
            db,
        )

        tu = TenantUser.query.filter_by(user_id=user_id, tenant_id=tenant_id).first()
        if not tu:
            return 0

        role = tu.role

        # 1. Admin bypass
        if role == Role.ADMIN:
            return PermissionBit.ALL

        user = db.session.get(User, user_id)
        if not user:
            return 0

        # Owner always gets ALL (unless view_only — see below)
        if role == Role.OWNER:
            mask = PermissionBit.ALL
        else:
            # Role default is the starting point
            default_mask = PermissionBit.ROLE_DEFAULTS.get(role.value, 0)

            # 2. all_planes row overrides the default
            all_row = UserAllAircraftAccess.query.filter_by(
                user_id=user_id, tenant_id=tenant_id
            ).first()

            if all_row is not None:
                mask = (
                    all_row.permissions_mask
                    if all_row.permissions_mask is not None
                    else default_mask
                )
            elif aircraft_id is not None:
                # 3. Per-aircraft row overrides the default
                ac_row = UserAircraftAccess.query.filter_by(
                    user_id=user_id, aircraft_id=aircraft_id
                ).first()
                if ac_row is not None:
                    mask = (
                        ac_row.permissions_mask
                        if ac_row.permissions_mask is not None
                        else default_mask
                    )
                else:
                    mask = 0  # no access row → no access
            else:
                # No all_planes row and no aircraft_id: use role default for list-level checks
                mask = default_mask

        # view_only suppresses all write bits
        if user.view_only:
            write_bits = (
                PermissionBit.EDIT_AIRCRAFT
                | PermissionBit.WRITE_MAINTENANCE
                | PermissionBit.EDIT_COMPONENTS
                | PermissionBit.WRITE_LOGBOOK
                | PermissionBit.RESERVE_AIRCRAFT
            )
            mask &= ~write_bits

        return mask

    @staticmethod
    def can(
        user_id: int,
        action: str,
        aircraft_id: int | None = None,
        tenant_id: int | None = None,
    ) -> bool:
        """Return True if the user holds the bit for *action* on the aircraft.

        When tenant_id is not supplied it is resolved from the user's TenantUser row.
        """
        from models import PermissionBit, TenantUser

        if tenant_id is None:
            tu = TenantUser.query.filter_by(user_id=user_id).first()
            if not tu:
                return False
            tenant_id = tu.tenant_id

        action_bits = {
            "view_aircraft": PermissionBit.VIEW_AIRCRAFT,
            "edit_aircraft": PermissionBit.EDIT_AIRCRAFT,
            "view_maintenance": PermissionBit.READ_MAINT_FULL
            | PermissionBit.READ_MAINT_LIMITED,
            "edit_maintenance": PermissionBit.WRITE_MAINTENANCE,
            "log_flight": PermissionBit.WRITE_LOGBOOK,
            "reserve_aircraft": PermissionBit.RESERVE_AIRCRAFT,
            "edit_components": PermissionBit.EDIT_COMPONENTS,
        }
        required = action_bits.get(action, 0)
        if required == 0:
            return False
        mask = AuthorizationService.effective_mask(user_id, aircraft_id, tenant_id)
        # For view_maintenance any of the two read bits is enough
        if action == "view_maintenance":
            return bool(mask & required)
        return (mask & required) == required

    @staticmethod
    def maintenance_view_level(user_id: int, aircraft_id: int, tenant_id: int) -> str:
        """Return 'full', 'limited', or 'none' for the user's maintenance read access."""
        from models import PermissionBit

        mask = AuthorizationService.effective_mask(user_id, aircraft_id, tenant_id)
        if mask & PermissionBit.READ_MAINT_FULL:
            return "full"
        if mask & PermissionBit.READ_MAINT_LIMITED:
            return "limited"
        return "none"
