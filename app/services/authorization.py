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
            Aircraft,
            PermissionBit,
            Role,
            TenantUser,
            User,
            UserAircraftAccess,
            UserAllAircraftAccess,
            db,
        )

        tu = TenantUser.query.filter_by(user_id=user_id, tenant_id=tenant_id).first()
        if not tu or tu.access_scope != "full" or tu.role is None:
            return 0
        if aircraft_id is not None:
            aircraft = db.session.get(Aircraft, aircraft_id)
            if aircraft is None or aircraft.tenant_id != tenant_id:
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
            # Role default is the starting point; the pilot / maintenance
            # flags add their profile's bits (an explicit mask still wins).
            default_mask = PermissionBit.ROLE_DEFAULTS.get(role.value, 0)
            if tu.is_pilot:
                default_mask |= PermissionBit.ROLE_DEFAULTS["pilot"]
            if tu.is_maintenance:
                default_mask |= PermissionBit.ROLE_DEFAULTS["maintenance"]

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

        # view_only (set on this membership) suppresses all write bits
        if tu.view_only:
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
        from models import PermissionBit, TenantUser, db

        if tenant_id is None:
            from flask import has_request_context
            from utils import current_membership

            if has_request_context():
                actor = current_membership()
                tu = (
                    db.session.get(TenantUser, (user_id, actor.tenant_id))
                    if actor
                    else None
                )
            else:
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


# Writes on an aircraft (its id in the URL) and the permission bit each needs
# (see docs/access-control.md). Owners and admins hold every bit; the other
# roles get their role's defaults, plus the pilot / maintenance flags', unless
# an admin set an explicit mask on their grant.
_EDIT_AIRCRAFT = (
    "aircraft.archive_aircraft",
    "aircraft.delete_aircraft",
    "aircraft.delete_photo",
    "aircraft.edit_aircraft",
    "aircraft.manage_owners",
    "aircraft.owner_record_payment",
    "aircraft.owner_reverse_entry",
    "aircraft.record_valuation_snapshot",
    "aircraft.reorder_photos",
    "aircraft.unarchive_aircraft",
    "aircraft.upload_photo",
    "aircraft.wb_config",
    "documents.delete_document",
    "documents.edit_document",
    "documents.upload_arc_cert",
    "documents.upload_document",
    "documents.upload_insurance_cert",
    "share.create_token",
    "share.revoke_token",
)
_EDIT_COMPONENTS = (
    "aircraft.delete_component",
    "aircraft.edit_component",
    "aircraft.new_component",
    "aircraft.quick_add_components",
)
_LOG_FLIGHT = (
    "aircraft.gps_import_confirm_all",
    "aircraft.gps_import_confirm_one",
    "aircraft.gps_import_rollback",
    "aircraft.gps_import_upload",
    "aircraft.wb_entry",
    "aircraft.wb_entry_delete",
    "flights.airframe_import_execute",
    "flights.airframe_import_review_resolve",
    "flights.airframe_import_review_resolve_all",
    "flights.airframe_import_rollback",
    "flights.airframe_import_toggle_historical",
    "flights.airframe_import_upload",
    "flights.delete_flight",
    "refuels.delete_refuel",
    "refuels.edit_refuel",
    "refuels.new_refuel",
)
_EDIT_MAINTENANCE = (
    "airworthiness.add_ad_sb_item",
    "airworthiness.add_avionics_unit",
    "airworthiness.add_document",
    "airworthiness.add_node",
    "airworthiness.add_stc",
    "airworthiness.add_wishlist_item",
    "airworthiness.delete_ad_sb_item",
    "airworthiness.delete_avionics_unit",
    "airworthiness.delete_document",
    "airworthiness.delete_node",
    "airworthiness.delete_stc",
    "airworthiness.delete_wishlist_item",
    "airworthiness.edit_ad_sb_item",
    "airworthiness.edit_avionics_unit",
    "airworthiness.trigger_sync",
    "airworthiness.update_status",
    "maintenance.add_amp_revision",
    "maintenance.delete_amp_revision",
    "maintenance.delete_trigger",
    "maintenance.edit_amp_declaration",
    "maintenance.edit_trigger",
    "maintenance.import_amp_commit",
    "maintenance.import_amp_rollback",
    "maintenance.import_amp_upload",
    "maintenance.new_trigger",
    "maintenance.service_trigger",
    "reservations.downtime_delete",
    "reservations.downtime_edit",
    "reservations.downtime_new",
)
_RESERVE = (
    "reservations.booking_settings",
    "reservations.cancel_reservation",
    "reservations.checkin",
    "reservations.checkout",
    "reservations.confirm_reservation",
    "reservations.decline_reservation",
    "reservations.edit_reservation",
    "reservations.new_reservation",
    "reservations.rental_charge",
)
AIRCRAFT_WRITE_ACTIONS: dict[str, str] = {
    **dict.fromkeys(_EDIT_AIRCRAFT, "edit_aircraft"),
    **dict.fromkeys(_EDIT_COMPONENTS, "edit_components"),
    **dict.fromkeys(_LOG_FLIGHT, "log_flight"),
    **dict.fromkeys(_EDIT_MAINTENANCE, "edit_maintenance"),
    **dict.fromkeys(_RESERVE, "reserve_aircraft"),
}

# Blueprints holding the hangar's operational data: a view-only membership
# changes nothing there (its own account, Places and Passports aside).
_VIEW_ONLY_BLOCKED = frozenset(
    {
        "aircraft",
        "airworthiness",
        "documents",
        "expenses",
        "flights",
        "maintenance",
        "pilots",
        "refuels",
        "reservations",
        "share",
        "snags",
    }
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def enforce_write_permissions() -> None:
    """before_request: refuse (403) a write the signed-in membership may not
    make — any write in the operational blueprints for a view-only member,
    and an aircraft write without its permission bit (AIRCRAFT_WRITE_ACTIONS).

    Admins bypass both, as in effective_mask. Requests without a membership,
    or naming an aircraft of another hangar, are left to the route (sign-in
    redirect, 404)."""
    from flask import abort, request
    from models import Aircraft, Role, db
    from utils import current_membership

    if request.method in _SAFE_METHODS or request.endpoint is None:
        return
    membership = current_membership()
    if membership is None or membership.role == Role.ADMIN:
        return
    if membership.view_only and request.blueprint in _VIEW_ONLY_BLOCKED:
        abort(403)
    action = AIRCRAFT_WRITE_ACTIONS.get(request.endpoint)
    aircraft_id = (request.view_args or {}).get("aircraft_id")
    if action is None or not isinstance(aircraft_id, int):
        return
    aircraft = db.session.get(Aircraft, aircraft_id)
    if aircraft is None or aircraft.tenant_id != membership.tenant_id:
        return
    if not AuthorizationService.can(
        membership.user_id, action, aircraft_id, membership.tenant_id
    ):
        abort(403)
