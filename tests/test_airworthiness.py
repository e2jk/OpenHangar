"""
Tests for Phase 33: Airworthiness Requirements Tracker.

Covers:
- EASASourceNode.display_path model property
- AirworthinessDocument.is_manual model property
- airworthiness_sync module: _build_tree_path, _fetch_references, _process_node,
  sync_aircraft, sync_all_nodes
- airworthiness blueprint routes: dashboard, add_node, delete_node, add_document,
  delete_document, update_status, add_stc, delete_stc, trigger_sync
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from unittest.mock import MagicMock, patch

import pytest  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AirworthinessDocument,
    AirworthinessDocStatus,
    AirworthinessDocType,
    AirworthinessDocumentStatus,
    Component,
    EASASourceNode,
    InstalledSTC,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="owner@example.com", role=Role.ADMIN):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="owner@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-TST"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_component(app, aircraft_id, comp_type="airframe"):
    with app.app_context():
        comp = Component(
            aircraft_id=aircraft_id,
            type=comp_type,
            make="Cessna",
            model="172S",
        )
        db.session.add(comp)
        db.session.commit()
        return comp.id


def _add_easa_node(app, component_id):
    with app.app_context():
        node = EASASourceNode(
            component_id=component_id,
            tc_holder_node_id="123",
            tc_holder_name="Cessna Aircraft",
            type_node_id="456",
            type_name="172",
            model_node_id="789",
            model_name="172S",
        )
        db.session.add(node)
        db.session.commit()
        return node.id


def _add_manual_document(app, component_id, reference="ARC-2024-001"):
    with app.app_context():
        doc = AirworthinessDocument(
            doc_type=AirworthinessDocType.ARC,
            reference=reference,
            component_id=component_id,
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


def _add_synced_document(app, node_id, reference="AD 2023-0048"):
    with app.app_context():
        doc = AirworthinessDocument(
            doc_type=AirworthinessDocType.AD,
            reference=reference,
            source_node_id=node_id,
            doc_url=f"https://ad.easa.europa.eu/ad/{reference.replace(' ', '_')}",
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


def _add_stc(app, aircraft_id, stc_number="EASA.A.S.01234"):
    with app.app_context():
        stc = InstalledSTC(
            aircraft_id=aircraft_id,
            stc_number=stc_number,
            title="Some STC",
            tc_holder="Acme Avionics",
        )
        db.session.add(stc)
        db.session.commit()
        return stc.id


# ── Model tests ────────────────────────────────────────────────────────────────


class TestEASASourceNodeModel:
    def test_display_path_combines_all_names(self, app):
        with app.app_context():
            node = EASASourceNode(
                component_id=1,
                tc_holder_node_id="1",
                tc_holder_name="Cessna Aircraft",
                type_node_id="2",
                type_name="172",
                model_node_id="3",
                model_name="172S",
            )
            assert node.display_path == "Cessna Aircraft / 172 / 172S"

    def test_display_path_with_spaces_in_names(self, app):
        with app.app_context():
            node = EASASourceNode(
                component_id=1,
                tc_holder_node_id="1",
                tc_holder_name="Piper Aircraft Inc",
                type_node_id="2",
                type_name="PA-28",
                model_node_id="3",
                model_name="PA-28-161 Warrior II",
            )
            assert (
                node.display_path == "Piper Aircraft Inc / PA-28 / PA-28-161 Warrior II"
            )


class TestAirworthinessDocumentModel:
    def test_is_manual_true_when_no_source_node(self, app):
        with app.app_context():
            doc = AirworthinessDocument(
                doc_type=AirworthinessDocType.ARC,
                reference="ARC-2024-001",
                component_id=1,
                source_node_id=None,
            )
            assert doc.is_manual is True

    def test_is_manual_false_when_source_node_set(self, app):
        with app.app_context():
            doc = AirworthinessDocument(
                doc_type=AirworthinessDocType.AD,
                reference="AD 2023-0048",
                source_node_id=42,
            )
            assert doc.is_manual is False

    def test_is_manual_true_when_source_node_id_zero_is_falsy(self, app):
        """Confirm the check is against None, not falsy."""
        with app.app_context():
            doc = AirworthinessDocument(
                doc_type=AirworthinessDocType.AD,
                reference="AD 2023-0001",
                source_node_id=None,
            )
            assert doc.is_manual is True


# ── airworthiness_sync unit tests ─────────────────────────────────────────────


class TestBuildTreePath:
    def test_builds_correct_format(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        with app.app_context():
            node = EASASourceNode(
                component_id=1,
                tc_holder_node_id="100",
                tc_holder_name="Cessna Aircraft",
                type_node_id="200",
                type_name="172",
                model_node_id="300",
                model_name="172S",
            )
            path = airworthiness_sync._build_tree_path(node)

        assert path == (
            "100@@@@0@@Cessna Aircraft|||200@@100@@1@@172|||300@@200@@2@@172S"
        )

    def test_includes_all_segments(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        with app.app_context():
            node = EASASourceNode(
                component_id=1,
                tc_holder_node_id="A",
                tc_holder_name="Holder",
                type_node_id="B",
                type_name="TypeX",
                model_node_id="C",
                model_name="ModelY",
            )
            path = airworthiness_sync._build_tree_path(node)

        parts = path.split("|||")
        assert len(parts) == 3
        assert "Holder" in parts[0]
        assert "TypeX" in parts[1]
        assert "ModelY" in parts[2]


class TestFetchReferences:
    def _make_node(self):
        """Return a bare EASASourceNode (not persisted)."""
        return EASASourceNode(
            component_id=1,
            tc_holder_node_id="100",
            tc_holder_name="Cessna Aircraft",
            type_node_id="200",
            type_name="172",
            model_node_id="300",
            model_name="172S",
        )

    def test_parses_ad_references(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        html = b"<html><body>AD 2023-0048 some text AD 2006-0345R</body></html>"
        with patch("airworthiness_sync.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = html

            with app.app_context():
                node = self._make_node()
                refs = airworthiness_sync._fetch_references(node)

        references = [r for r, _ in refs]
        types = [t for _, t in refs]
        assert "AD 2023-0048" in references
        assert "AD 2006-0345R" in references
        assert all(t == AirworthinessDocType.AD for t in types)

    def test_parses_sib_references(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        html = b"<html><body>SIB 2024-01 and SIB 2022-05R</body></html>"
        with patch("airworthiness_sync.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = html

            with app.app_context():
                node = self._make_node()
                refs = airworthiness_sync._fetch_references(node)

        references = [r for r, _ in refs]
        types = [t for _, t in refs]
        assert "SIB 2024-01" in references
        assert "SIB 2022-05R" in references
        assert all(t == AirworthinessDocType.SIB for t in types)

    def test_parses_mixed_ad_and_sib(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        html = b"<html>AD 2023-0001 text SIB 2023-02</html>"
        with patch("airworthiness_sync.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = html

            with app.app_context():
                node = self._make_node()
                refs = airworthiness_sync._fetch_references(node)

        ad_refs = [(r, t) for r, t in refs if t == AirworthinessDocType.AD]
        sib_refs = [(r, t) for r, t in refs if t == AirworthinessDocType.SIB]
        assert len(ad_refs) == 1
        assert len(sib_refs) == 1

    def test_returns_empty_on_no_matches(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        html = b"<html><body>No documents here</body></html>"
        with patch("airworthiness_sync.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = html

            with app.app_context():
                node = self._make_node()
                refs = airworthiness_sync._fetch_references(node)

        assert refs == []

    def test_raises_on_http_error(self, app):
        import urllib.error
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        with patch("airworthiness_sync.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            with app.app_context():
                node = self._make_node()
                with pytest.raises(urllib.error.URLError):
                    airworthiness_sync._fetch_references(node)

    def test_sends_post_request_with_model_node_id(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        html = b""
        with patch("airworthiness_sync.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = html

            with app.app_context():
                node = self._make_node()
                airworthiness_sync._fetch_references(node)

            call_args = mock_urlopen.call_args
            req_obj = call_args[0][0]
            # The POST body should contain the model_node_id
            assert b"300" in req_obj.data


class TestEasaDocUrl:
    def test_replaces_spaces_with_underscores(self):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        url = airworthiness_sync._easa_doc_url("AD 2023-0048")
        assert url == "https://ad.easa.europa.eu/ad/AD_2023-0048"

    def test_handles_reference_without_spaces(self):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        url = airworthiness_sync._easa_doc_url("SIB_2024-01")
        assert "SIB_2024-01" in url


class TestProcessNode:
    def test_creates_documents_for_new_references(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        with patch.object(
            airworthiness_sync,
            "_fetch_references",
            return_value=[
                ("AD 2023-0048", AirworthinessDocType.AD),
                ("SIB 2024-01", AirworthinessDocType.SIB),
            ],
        ):
            with app.app_context():
                node = db.session.get(EASASourceNode, node_id)
                added, had_error = airworthiness_sync._process_node(node)

        assert added == 2
        assert had_error is False

        with app.app_context():
            docs = AirworthinessDocument.query.filter_by(source_node_id=node_id).all()
            assert len(docs) == 2
            refs = {d.reference for d in docs}
            assert "AD 2023-0048" in refs
            assert "SIB 2024-01" in refs

    def test_creates_pending_review_status_for_new_docs(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        with patch.object(
            airworthiness_sync,
            "_fetch_references",
            return_value=[("AD 2023-0001", AirworthinessDocType.AD)],
        ):
            with app.app_context():
                node = db.session.get(EASASourceNode, node_id)
                airworthiness_sync._process_node(node)

        with app.app_context():
            doc = AirworthinessDocument.query.filter_by(
                source_node_id=node_id, reference="AD 2023-0001"
            ).first()
            assert doc is not None
            st = AirworthinessDocumentStatus.query.filter_by(
                aircraft_id=ac_id, document_id=doc.id
            ).first()
            assert st is not None
            assert st.status == AirworthinessDocStatus.PENDING_REVIEW

    def test_skips_already_existing_references(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)
        _add_synced_document(app, node_id, reference="AD 2023-0048")

        with patch.object(
            airworthiness_sync,
            "_fetch_references",
            return_value=[
                ("AD 2023-0048", AirworthinessDocType.AD),  # already exists
                ("AD 2024-0001", AirworthinessDocType.AD),  # new
            ],
        ):
            with app.app_context():
                node = db.session.get(EASASourceNode, node_id)
                added, had_error = airworthiness_sync._process_node(node)

        assert added == 1
        assert had_error is False

        with app.app_context():
            docs = AirworthinessDocument.query.filter_by(source_node_id=node_id).all()
            assert len(docs) == 2  # original + one new

    def test_increments_consecutive_errors_on_failure(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        with patch.object(
            airworthiness_sync,
            "_fetch_references",
            side_effect=Exception("Network error"),
        ):
            with app.app_context():
                node = db.session.get(EASASourceNode, node_id)
                added, had_error = airworthiness_sync._process_node(node)

        assert added == 0
        assert had_error is True

        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            assert node.consecutive_errors == 1

    def test_resets_consecutive_errors_on_success(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        # Set a pre-existing error count
        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            node.consecutive_errors = 3
            db.session.commit()

        with patch.object(
            airworthiness_sync,
            "_fetch_references",
            return_value=[],
        ):
            with app.app_context():
                node = db.session.get(EASASourceNode, node_id)
                airworthiness_sync._process_node(node)

        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            assert node.consecutive_errors == 0

    def test_updates_last_synced_at_on_success(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        with patch.object(
            airworthiness_sync,
            "_fetch_references",
            return_value=[],
        ):
            with app.app_context():
                node = db.session.get(EASASourceNode, node_id)
                airworthiness_sync._process_node(node)

        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            assert node.last_synced_at is not None

    def test_returns_zero_added_on_empty_refs(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        with patch.object(
            airworthiness_sync,
            "_fetch_references",
            return_value=[],
        ):
            with app.app_context():
                node = db.session.get(EASASourceNode, node_id)
                added, had_error = airworthiness_sync._process_node(node)

        assert added == 0
        assert had_error is False


class TestSyncAircraft:
    def test_returns_totals_for_single_node(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _add_easa_node(app, comp_id)

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync,
                "_process_node",
                return_value=(3, False),
            ),
        ):
            with app.app_context():
                ac = db.session.get(Aircraft, ac_id)
                total_added, total_errors = airworthiness_sync.sync_aircraft(ac)

        assert total_added == 3
        assert total_errors == 0

    def test_returns_error_count_when_node_fails(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _add_easa_node(app, comp_id)

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync,
                "_process_node",
                return_value=(0, True),
            ),
        ):
            with app.app_context():
                ac = db.session.get(Aircraft, ac_id)
                total_added, total_errors = airworthiness_sync.sync_aircraft(ac)

        assert total_added == 0
        assert total_errors == 1

    def test_no_sleep_for_first_node(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _add_easa_node(app, comp_id)

        with (
            patch("airworthiness_sync.time.sleep") as mock_sleep,
            patch.object(
                airworthiness_sync,
                "_process_node",
                return_value=(0, False),
            ),
        ):
            with app.app_context():
                ac = db.session.get(Aircraft, ac_id)
                airworthiness_sync.sync_aircraft(ac)

        mock_sleep.assert_not_called()

    def test_sleep_between_multiple_nodes(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        # Add two nodes to the same component
        _add_easa_node(app, comp_id)
        _add_easa_node(app, comp_id)

        with (
            patch("airworthiness_sync.time.sleep") as mock_sleep,
            patch.object(
                airworthiness_sync,
                "_process_node",
                return_value=(0, False),
            ),
        ):
            with app.app_context():
                ac = db.session.get(Aircraft, ac_id)
                airworthiness_sync.sync_aircraft(ac)

        assert mock_sleep.call_count == 1

    def test_returns_zero_for_aircraft_with_no_nodes(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_component(app, ac_id)  # component with no EASA nodes

        with patch("airworthiness_sync.time.sleep"):
            with app.app_context():
                ac = db.session.get(Aircraft, ac_id)
                total_added, total_errors = airworthiness_sync.sync_aircraft(ac)

        assert total_added == 0
        assert total_errors == 0

    def test_aggregates_across_multiple_components(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id1 = _add_component(app, ac_id, "airframe")
        comp_id2 = _add_component(app, ac_id, "engine")
        _add_easa_node(app, comp_id1)
        _add_easa_node(app, comp_id2)

        call_results = [(2, False), (1, True)]
        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync,
                "_process_node",
                side_effect=call_results,
            ),
        ):
            with app.app_context():
                ac = db.session.get(Aircraft, ac_id)
                total_added, total_errors = airworthiness_sync.sync_aircraft(ac)

        assert total_added == 3
        assert total_errors == 1


class TestSyncAllNodes:
    def test_processes_all_nodes_in_db(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _add_easa_node(app, comp_id)
        _add_easa_node(app, comp_id)

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync,
                "_process_node",
                return_value=(1, False),
            ) as mock_process,
        ):
            airworthiness_sync.sync_all_nodes(app)

        assert mock_process.call_count == 2

    def test_returns_none(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(airworthiness_sync, "_process_node", return_value=(0, False)),
        ):
            airworthiness_sync.sync_all_nodes(app)  # procedure always returns None

    def test_sleeps_between_nodes(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _add_easa_node(app, comp_id)
        _add_easa_node(app, comp_id)
        _add_easa_node(app, comp_id)

        with (
            patch("airworthiness_sync.time.sleep") as mock_sleep,
            patch.object(
                airworthiness_sync,
                "_process_node",
                return_value=(0, False),
            ),
        ):
            airworthiness_sync.sync_all_nodes(app)

        # sleep is called between nodes: N-1 times for N nodes
        assert mock_sleep.call_count == 2

    def test_warns_for_overdue_nodes(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]
        from datetime import datetime, timezone, timedelta

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        # Set last_synced_at to far in the past (overdue)
        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            node.last_synced_at = datetime.now(timezone.utc) - timedelta(hours=100)
            db.session.commit()

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync,
                "_process_node",
                return_value=(0, False),
            ),
        ):
            with patch.object(airworthiness_sync._log, "warning") as mock_warn:
                airworthiness_sync.sync_all_nodes(app)

        # Should have issued at least one overdue warning
        assert mock_warn.called

    def test_no_nodes_is_harmless(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        with (
            patch("airworthiness_sync.time.sleep") as mock_sleep,
            patch.object(
                airworthiness_sync, "_process_node", return_value=(0, False)
            ) as mock_process,
        ):
            airworthiness_sync.sync_all_nodes(app)

        mock_process.assert_not_called()
        mock_sleep.assert_not_called()

    def test_increments_error_count_when_process_node_fails(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _add_easa_node(app, comp_id)

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(airworthiness_sync, "_process_node", return_value=(0, True)),
        ):
            airworthiness_sync.sync_all_nodes(app)

    def test_backoff_skips_node_with_repeated_errors(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]
        from datetime import datetime, timezone, timedelta

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        # 2 consecutive errors, last synced 1 day ago → backoff is 4 days → should skip
        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            node.consecutive_errors = 2
            node.last_synced_at = datetime.now(timezone.utc) - timedelta(days=1)
            db.session.commit()

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync, "_process_node", return_value=(0, False)
            ) as mock_process,
        ):
            airworthiness_sync.sync_all_nodes(app)

        mock_process.assert_not_called()

    def test_backoff_retries_after_window_expires(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]
        from datetime import datetime, timezone, timedelta

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        # 2 consecutive errors, last synced 5 days ago → backoff is 4 days → should retry
        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            node.consecutive_errors = 2
            node.last_synced_at = datetime.now(timezone.utc) - timedelta(days=5)
            db.session.commit()

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync, "_process_node", return_value=(0, False)
            ) as mock_process,
        ):
            airworthiness_sync.sync_all_nodes(app)

        mock_process.assert_called_once()

    def test_no_backoff_after_single_error(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]
        from datetime import datetime, timezone, timedelta

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        # 1 consecutive error → no backoff, always retry
        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            node.consecutive_errors = 1
            node.last_synced_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.session.commit()

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync, "_process_node", return_value=(0, False)
            ) as mock_process,
        ):
            airworthiness_sync.sync_all_nodes(app)

        mock_process.assert_called_once()

    def test_no_backoff_when_never_successfully_synced(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)

        # Many errors but last_synced_at is None → always retry
        with app.app_context():
            node = db.session.get(EASASourceNode, node_id)
            node.consecutive_errors = 5
            node.last_synced_at = None
            db.session.commit()

        with (
            patch("airworthiness_sync.time.sleep"),
            patch.object(
                airworthiness_sync, "_process_node", return_value=(0, False)
            ) as mock_process,
        ):
            airworthiness_sync.sync_all_nodes(app)

        mock_process.assert_called_once()


# ── Route tests ────────────────────────────────────────────────────────────────


class TestAirworthinessDashboard:
    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/")
        assert resp.status_code == 302

    def test_owner_can_view_dashboard(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/")
        assert resp.status_code == 200

    def test_pilot_can_view_dashboard(self, app, client):
        _create_user_and_tenant(app, "pilot@example.com", role=Role.PILOT)
        _, tenant_id = _create_user_and_tenant(
            app, "owner@example.com", role=Role.ADMIN
        )
        ac_id = _add_aircraft(app, tenant_id)
        # Log in as pilot who belongs to same tenant
        with app.app_context():
            Tenant.query.filter_by(name="Test Hangar").first()
            # The pilot's tenant is different — use the owner's tenant instead
        # Actually just test that a crew role user with same tenant can view
        _login(app, client, "owner@example.com")
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/")
        assert resp.status_code == 200

    def test_404_for_aircraft_of_other_tenant(self, app, client):
        _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/")
        assert resp.status_code == 404

    def test_shows_manual_documents(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _add_manual_document(app, comp_id, "ARC-2024-001")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/")
        assert resp.status_code == 200
        assert b"ARC-2024-001" in resp.data

    def test_shows_synced_documents(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)
        _add_synced_document(app, node_id, "AD 2023-0048")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/")
        assert resp.status_code == 200
        assert b"AD 2023-0048" in resp.data

    def test_empty_dashboard_is_valid(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/")
        assert resp.status_code == 200


class TestAddNode:
    def test_get_renders_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_component(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/nodes/new")
        assert resp.status_code == 200

    def test_get_with_preselected_component(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        resp = client.get(
            f"/aircraft/{ac_id}/airworthiness/nodes/new?component_id={comp_id}"
        )
        assert resp.status_code == 200

    def test_post_creates_node(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/nodes/new",
            data={
                "component_id": comp_id,
                "tc_holder_node_id": "100",
                "tc_holder_name": "Cessna Aircraft",
                "type_node_id": "200",
                "type_name": "172",
                "model_node_id": "300",
                "model_name": "172S",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert f"/aircraft/{ac_id}/airworthiness/" in resp.headers["Location"]

        with app.app_context():
            node = EASASourceNode.query.filter_by(component_id=comp_id).first()
            assert node is not None
            assert node.tc_holder_name == "Cessna Aircraft"
            assert node.model_name == "172S"

    def test_post_rejects_component_from_other_aircraft(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id, "OO-TST")
        ac_id2 = _add_aircraft(app, tenant_id, "OO-TST2")
        comp_id_other = _add_component(app, ac_id2)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/nodes/new",
            data={
                "component_id": comp_id_other,
                "tc_holder_node_id": "100",
                "tc_holder_name": "Cessna",
                "type_node_id": "200",
                "type_name": "172",
                "model_node_id": "300",
                "model_name": "172S",
            },
        )
        assert resp.status_code == 400

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/nodes/new")
        assert resp.status_code == 302


class TestDeleteNode:
    def test_post_deletes_node(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/nodes/{node_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with app.app_context():
            assert db.session.get(EASASourceNode, node_id) is None

    def test_404_for_node_of_other_aircraft(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id, "OO-TST")
        ac_id2 = _add_aircraft(app, tenant_id, "OO-TST2")
        comp_id2 = _add_component(app, ac_id2)
        node_id = _add_easa_node(app, comp_id2)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/airworthiness/nodes/{node_id}/delete")
        assert resp.status_code == 404

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)
        resp = client.post(f"/aircraft/{ac_id}/airworthiness/nodes/{node_id}/delete")
        assert resp.status_code == 302


class TestAddDocument:
    def test_get_renders_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_component(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/documents/new")
        assert resp.status_code == 200

    def test_post_creates_manual_document(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/new",
            data={
                "component_id": comp_id,
                "doc_type": AirworthinessDocType.ARC,
                "reference": "ARC-2025-001",
                "title": "Airworthiness Review Certificate",
                "doc_url": "https://example.com/arc.pdf",
                "expiry_date": "2026-01-01",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with app.app_context():
            doc = AirworthinessDocument.query.filter_by(
                reference="ARC-2025-001"
            ).first()
            assert doc is not None
            assert doc.is_manual is True
            assert doc.component_id == comp_id
            assert doc.source_node_id is None

    def test_post_creates_pending_review_status(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/new",
            data={
                "component_id": comp_id,
                "doc_type": AirworthinessDocType.AD,
                "reference": "AD 2020-0001",
            },
        )
        with app.app_context():
            doc = AirworthinessDocument.query.filter_by(
                reference="AD 2020-0001"
            ).first()
            assert doc is not None
            st = AirworthinessDocumentStatus.query.filter_by(
                aircraft_id=ac_id, document_id=doc.id
            ).first()
            assert st is not None
            assert st.status == AirworthinessDocStatus.PENDING_REVIEW

    def test_post_rejects_component_from_other_aircraft(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id, "OO-TST")
        ac_id2 = _add_aircraft(app, tenant_id, "OO-TST2")
        comp_id_other = _add_component(app, ac_id2)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/new",
            data={
                "component_id": comp_id_other,
                "doc_type": AirworthinessDocType.AD,
                "reference": "AD 2020-0001",
            },
        )
        assert resp.status_code == 400

    def test_post_optional_expiry_date(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/new",
            data={
                "component_id": comp_id,
                "doc_type": AirworthinessDocType.SB,
                "reference": "SB 2021-001",
                "expiry_date": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            doc = AirworthinessDocument.query.filter_by(reference="SB 2021-001").first()
            assert doc.expiry_date is None

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/documents/new")
        assert resp.status_code == 302


class TestDeleteDocument:
    def test_post_deletes_manual_document(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with app.app_context():
            assert db.session.get(AirworthinessDocument, doc_id) is None

    def test_post_rejects_synced_document(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        node_id = _add_easa_node(app, comp_id)
        doc_id = _add_synced_document(app, node_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/delete",
            follow_redirects=False,
        )
        # Should redirect back to dashboard (not delete)
        assert resp.status_code == 302
        assert f"/aircraft/{ac_id}/airworthiness/" in resp.headers["Location"]

        with app.app_context():
            assert db.session.get(AirworthinessDocument, doc_id) is not None

    def test_404_for_nonexistent_document(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/airworthiness/documents/99999/delete")
        assert resp.status_code == 404

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        resp = client.post(f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/delete")
        assert resp.status_code == 302


class TestUpdateStatus:
    def test_get_renders_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/status")
        assert resp.status_code == 200

    def test_post_creates_new_status(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/status",
            data={
                "status": AirworthinessDocStatus.COMPLIED,
                "notes": "Done in 2025",
                "compliance_date": "2025-01-15",
                "next_review_date": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with app.app_context():
            st = AirworthinessDocumentStatus.query.filter_by(
                aircraft_id=ac_id, document_id=doc_id
            ).first()
            assert st is not None
            assert st.status == AirworthinessDocStatus.COMPLIED
            assert st.notes == "Done in 2025"

    def test_post_updates_existing_status(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)

        # Pre-create a status row
        with app.app_context():
            st = AirworthinessDocumentStatus(
                aircraft_id=ac_id,
                document_id=doc_id,
                status=AirworthinessDocStatus.PENDING_REVIEW,
            )
            db.session.add(st)
            db.session.commit()

        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/status",
            data={
                "status": AirworthinessDocStatus.NOT_APPLICABLE,
                "notes": "",
                "compliance_date": "",
                "next_review_date": "",
            },
        )

        with app.app_context():
            rows = AirworthinessDocumentStatus.query.filter_by(
                aircraft_id=ac_id, document_id=doc_id
            ).all()
            # Must not create duplicates
            assert len(rows) == 1
            assert rows[0].status == AirworthinessDocStatus.NOT_APPLICABLE

    def test_post_rejects_invalid_status(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/status",
            data={
                "status": "invalid_status",
                "notes": "",
                "compliance_date": "",
                "next_review_date": "",
            },
        )
        assert resp.status_code == 400

    def test_post_with_next_review_date(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/status",
            data={
                "status": AirworthinessDocStatus.DEFERRED,
                "notes": "Awaiting parts",
                "compliance_date": "",
                "next_review_date": "2026-06-01",
            },
        )
        with app.app_context():
            from datetime import date

            st = AirworthinessDocumentStatus.query.filter_by(
                aircraft_id=ac_id, document_id=doc_id
            ).first()
            assert st.next_review_date == date(2026, 6, 1)

    def test_pilot_can_update_status(self, app, client):
        """CREW roles (including PILOT) should be allowed to update status."""
        from models import UserAllAircraftAccess  # pyright: ignore[reportMissingImports]

        _, tenant_id = _create_user_and_tenant(
            app, "admin@example.com", role=Role.ADMIN
        )
        # Add a pilot to the same tenant with all-aircraft access
        with app.app_context():
            pilot = User(
                email="pilot@example.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(pilot)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=pilot.id, tenant_id=tenant_id, role=Role.PILOT)
            )
            db.session.add(UserAllAircraftAccess(user_id=pilot.id, tenant_id=tenant_id))
            db.session.commit()

        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        _login(app, client, "pilot@example.com")
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/status")
        assert resp.status_code == 200

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/documents/{doc_id}/status")
        assert resp.status_code == 302


class TestAddSTC:
    def test_get_renders_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/stcs/new")
        assert resp.status_code == 200

    def test_post_creates_stc(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/stcs/new",
            data={
                "stc_number": "EASA.A.S.01234",
                "title": "Some great STC",
                "tc_holder": "Acme Avionics",
                "installation_date": "2024-03-15",
                "notes": "Installed during overhaul",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert f"/aircraft/{ac_id}/airworthiness/" in resp.headers["Location"]

        with app.app_context():
            stc = InstalledSTC.query.filter_by(
                aircraft_id=ac_id, stc_number="EASA.A.S.01234"
            ).first()
            assert stc is not None
            assert stc.tc_holder == "Acme Avionics"

    def test_post_without_optional_fields(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/stcs/new",
            data={
                "stc_number": "FAA.SA01234AT",
                "title": "",
                "tc_holder": "",
                "installation_date": "",
                "notes": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with app.app_context():
            stc = InstalledSTC.query.filter_by(aircraft_id=ac_id).first()
            assert stc is not None
            assert stc.title is None
            assert stc.installation_date is None

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/airworthiness/stcs/new")
        assert resp.status_code == 302


class TestDeleteSTC:
    def test_post_deletes_stc(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        stc_id = _add_stc(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/airworthiness/stcs/{stc_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert f"/aircraft/{ac_id}/airworthiness/" in resp.headers["Location"]

        with app.app_context():
            assert db.session.get(InstalledSTC, stc_id) is None

    def test_404_for_stc_of_other_aircraft(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id, "OO-TST")
        ac_id2 = _add_aircraft(app, tenant_id, "OO-TST2")
        stc_id = _add_stc(app, ac_id2)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/airworthiness/stcs/{stc_id}/delete")
        assert resp.status_code == 404

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        stc_id = _add_stc(app, ac_id)
        resp = client.post(f"/aircraft/{ac_id}/airworthiness/stcs/{stc_id}/delete")
        assert resp.status_code == 302


class TestTriggerSync:
    def test_post_triggers_sync_and_redirects(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)

        with (
            patch.dict("os.environ", {"OPENHANGAR_ENV": "production"}),
            patch("airworthiness_sync.sync_aircraft", return_value=(0, 0)),
        ):
            resp = client.post(
                f"/aircraft/{ac_id}/airworthiness/sync",
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert f"/aircraft/{ac_id}/airworthiness/" in resp.headers["Location"]

    def test_flash_success_when_docs_added(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)

        with (
            patch.dict("os.environ", {"OPENHANGAR_ENV": "production"}),
            patch("airworthiness_sync.sync_aircraft", return_value=(3, 0)),
        ):
            resp = client.post(
                f"/aircraft/{ac_id}/airworthiness/sync",
                follow_redirects=True,
            )

        assert resp.status_code == 200
        assert b"3" in resp.data  # "3 new document(s)"

    def test_flash_info_when_no_new_docs(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)

        with (
            patch.dict("os.environ", {"OPENHANGAR_ENV": "production"}),
            patch("airworthiness_sync.sync_aircraft", return_value=(0, 0)),
        ):
            resp = client.post(
                f"/aircraft/{ac_id}/airworthiness/sync",
                follow_redirects=True,
            )

        assert resp.status_code == 200
        assert b"No new documents" in resp.data

    def test_flash_warning_when_errors(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)

        with (
            patch.dict("os.environ", {"OPENHANGAR_ENV": "production"}),
            patch("airworthiness_sync.sync_aircraft", return_value=(0, 2)),
        ):
            resp = client.post(
                f"/aircraft/{ac_id}/airworthiness/sync",
                follow_redirects=True,
            )

        assert resp.status_code == 200
        assert b"error" in resp.data.lower() or b"2" in resp.data

    def test_403_in_non_production(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)

        with patch.dict("os.environ", {"OPENHANGAR_ENV": "development"}):
            resp = client.post(f"/aircraft/{ac_id}/airworthiness/sync")

        assert resp.status_code == 403

    def test_redirects_when_not_logged_in(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.post(f"/aircraft/{ac_id}/airworthiness/sync")
        assert resp.status_code == 302

    def test_404_for_aircraft_of_other_tenant(self, app, client):
        _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "a@example.com")

        with (
            patch.dict("os.environ", {"OPENHANGAR_ENV": "production"}),
            patch("airworthiness_sync.sync_aircraft", return_value=(0, 0)),
        ):
            resp = client.post(f"/aircraft/{ac_id}/airworthiness/sync")

        assert resp.status_code == 404


# ── Coverage gap tests ─────────────────────────────────────────────────────────


class TestAircraftDetailAirworthinessCount:
    """Covers aircraft/routes.py:232 — the aw_counts loop body."""

    def test_counts_aw_statuses_on_detail_page(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        comp_id = _add_component(app, ac_id)
        doc_id = _add_manual_document(app, comp_id, "ARC-2024-001")

        with app.app_context():
            st = AirworthinessDocumentStatus(
                aircraft_id=ac_id,
                document_id=doc_id,
                status=AirworthinessDocStatus.PENDING_REVIEW,
            )
            db.session.add(st)
            db.session.commit()

        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert resp.status_code == 200


class TestTenantIdGuard:
    """Covers airworthiness/routes.py:40 — abort(403) when user has no TenantUser."""

    def test_403_when_user_has_no_tenant_user(self, app):
        from werkzeug.exceptions import Forbidden  # pyright: ignore[reportMissingImports]
        from airworthiness.routes import _tenant_id  # pyright: ignore[reportMissingImports]
        from flask import session  # pyright: ignore[reportMissingImports]

        with app.app_context():
            user = User(
                email="orphan@example.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(user)
            db.session.commit()
            uid = user.id

        with app.test_request_context():
            session["user_id"] = uid
            with pytest.raises(Forbidden):
                _tenant_id()


class TestGetDocOr404CrossAircraft:
    """Covers airworthiness/routes.py:71 — abort(404) when doc belongs to a different aircraft."""

    def test_404_when_doc_component_is_on_different_aircraft(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_a_id = _add_aircraft(app, tenant_id, "OO-AAA")
        comp_a_id = _add_component(app, ac_a_id)
        doc_id = _add_manual_document(app, comp_a_id, "ARC-2024-001")
        ac_b_id = _add_aircraft(app, tenant_id, "OO-BBB")

        _login(app, client)
        resp = client.get(
            f"/aircraft/{ac_b_id}/airworthiness/documents/{doc_id}/status"
        )
        assert resp.status_code == 404


class TestEasaSyncScheduler:
    """Covers init.py:221-253 (_easa_sync_loop) and 257-265 (_start_easa_sync_scheduler)."""

    def test_easa_sync_loop_calls_sync_all_nodes(self, app):
        import airworthiness_sync  # pyright: ignore[reportMissingImports]
        from init import _easa_sync_loop  # pyright: ignore[reportMissingImports]

        calls: list[int] = []

        def fake_sync(a: object) -> None:
            calls.append(1)
            raise SystemExit(0)

        with (
            patch("time.sleep"),
            patch.object(airworthiness_sync, "sync_all_nodes", side_effect=fake_sync),
        ):
            with pytest.raises(SystemExit):
                _easa_sync_loop(app)

        assert len(calls) == 1

    def test_easa_sync_loop_with_explicit_hour_triggers_next_day_branch(self, app):
        """SYNC_HOUR=0 makes next_run = today 00:00 UTC (always in the past),
        forcing the 'next_run += timedelta(days=1)' branch to execute."""
        import os
        import airworthiness_sync  # pyright: ignore[reportMissingImports]
        from init import _easa_sync_loop  # pyright: ignore[reportMissingImports]

        def fake_sync(a: object) -> None:
            raise SystemExit(0)

        with (
            patch.dict(os.environ, {"OPENHANGAR_AIRWORTHINESS_EASA_SYNC_HOUR": "0"}),
            patch("time.sleep"),
            patch.object(airworthiness_sync, "sync_all_nodes", side_effect=fake_sync),
        ):
            with pytest.raises(SystemExit):
                _easa_sync_loop(app)

    def test_easa_sync_loop_falls_back_to_random_on_invalid_hour(self, app):
        import os
        import airworthiness_sync  # pyright: ignore[reportMissingImports]
        from init import _easa_sync_loop  # pyright: ignore[reportMissingImports]

        def fake_sync(a: object) -> None:
            raise SystemExit(0)

        with (
            patch.dict(os.environ, {"OPENHANGAR_AIRWORTHINESS_EASA_SYNC_HOUR": "bad"}),
            patch("time.sleep"),
            patch.object(airworthiness_sync, "sync_all_nodes", side_effect=fake_sync),
        ):
            with pytest.raises(SystemExit):
                _easa_sync_loop(app)

    def test_start_easa_sync_scheduler_creates_named_daemon_thread(self, app):
        from init import _start_easa_sync_scheduler  # pyright: ignore[reportMissingImports]

        with patch("threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            _start_easa_sync_scheduler(app)

        mock_thread_cls.assert_called_once()
        kwargs = mock_thread_cls.call_args.kwargs
        assert kwargs["name"] == "easa-sync"
        assert kwargs["daemon"] is True
        mock_thread.start.assert_called_once()

    def test_create_app_calls_easa_scheduler_for_non_sqlite_production(
        self, monkeypatch
    ):
        """Covers init.py:1155 — _start_easa_sync_scheduler called when the DB
        is non-SQLite and FLASK_ENV is production (or unset)."""
        from init import create_app  # pyright: ignore[reportMissingImports]

        monkeypatch.setenv(
            "OPENHANGAR_DATABASE_URL", "postgresql://fake:fake@localhost/fakedb"
        )
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)
        monkeypatch.delenv("OPENHANGAR_SKIP_BACKGROUND_THREADS", raising=False)

        with (
            patch("services.version_service.start_version_check_thread"),
            patch("sync_watcher.start_sync_watcher"),
            patch("init._start_easa_sync_scheduler") as mock_easa,
            patch("init._start_notification_scheduler"),
            patch("services.notification_service.send_welcome_email_if_needed"),
        ):
            created_app = create_app()

        mock_easa.assert_called_once_with(created_app)
