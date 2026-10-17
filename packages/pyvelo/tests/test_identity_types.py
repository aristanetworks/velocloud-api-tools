"""Tests for identity key types (frozen dataclasses) and identity dataclasses."""

import uuid

import pytest
from pyvelo.core.identity import (
    AddressGroupId,
    AddressGroupIdentity,
    AddressGroupLogicalId,
    AddressGroupName,
    ApplicationId,
    ApplicationIdentity,
    ApplicationName,
    EdgeId,
    EdgeIdentity,
    EdgeLogicalId,
    EdgeName,
    LicenseId,
    LicenseIdentity,
    LicenseName,
    LicenseSku,
    ProfileId,
    ProfileIdentity,
    ProfileName,
    SegmentId,
    SegmentIdentity,
    SegmentLogicalId,
    SegmentName,
    SegmentObjectId,
    ServiceGroupId,
    ServiceGroupIdentity,
    ServiceGroupLogicalId,
    ServiceGroupName,
)


class TestKeyTypes:
    """Key types are frozen dataclasses with a .value field and .identity_type ClassVar."""

    def test_segment_id_is_frozen_dataclass(self):
        sid = SegmentId(42)
        assert sid.value == 42
        with pytest.raises(AttributeError):
            setattr(sid, "value", 99)

    def test_segment_id_not_int(self):
        sid = SegmentId(42)
        assert not isinstance(sid, int)

    def test_segment_id_identity_type(self):
        assert SegmentId.identity_type is SegmentIdentity

    def test_segment_object_id(self):
        soid = SegmentObjectId(100)
        assert soid.value == 100
        assert SegmentObjectId.identity_type is SegmentIdentity

    def test_segment_logical_id(self):
        u = uuid.uuid4()
        slid = SegmentLogicalId(u)
        assert slid.value == u
        assert SegmentLogicalId.identity_type is SegmentIdentity

    def test_segment_name(self):
        sn = SegmentName("Global Segment")
        assert sn.value == "Global Segment"
        assert SegmentName.identity_type is SegmentIdentity

    def test_edge_id(self):
        eid = EdgeId(1)
        assert eid.value == 1
        assert EdgeId.identity_type is EdgeIdentity

    def test_edge_logical_id(self):
        u = uuid.uuid4()
        elid = EdgeLogicalId(u)
        assert elid.value == u
        assert EdgeLogicalId.identity_type is EdgeIdentity

    def test_edge_name(self):
        en = EdgeName("Branch-NYC")
        assert en.value == "Branch-NYC"
        assert EdgeName.identity_type is EdgeIdentity

    def test_profile_id(self):
        pid = ProfileId(10)
        assert pid.value == 10
        assert ProfileId.identity_type is ProfileIdentity

    def test_profile_name(self):
        pn = ProfileName("Default")
        assert pn.value == "Default"
        assert ProfileName.identity_type is ProfileIdentity

    def test_application_id(self):
        aid = ApplicationId(42)
        assert aid.value == 42
        assert ApplicationId.identity_type is ApplicationIdentity

    def test_application_name(self):
        an = ApplicationName("Zoom")
        assert an.value == "Zoom"
        assert ApplicationName.identity_type is ApplicationIdentity

    def test_address_group_id(self):
        agid = AddressGroupId(5)
        assert agid.value == 5
        assert AddressGroupId.identity_type is AddressGroupIdentity

    def test_address_group_logical_id(self):
        u = uuid.uuid4()
        aglid = AddressGroupLogicalId(u)
        assert aglid.value == u

    def test_address_group_name(self):
        agn = AddressGroupName("RFC1918")
        assert agn.value == "RFC1918"

    def test_service_group_id(self):
        sgid = ServiceGroupId(7)
        assert sgid.value == 7
        assert ServiceGroupId.identity_type is ServiceGroupIdentity

    def test_service_group_logical_id(self):
        u = uuid.uuid4()
        sglid = ServiceGroupLogicalId(u)
        assert sglid.value == u

    def test_service_group_name(self):
        sgn = ServiceGroupName("Web Ports")
        assert sgn.value == "Web Ports"

    def test_license_id(self):
        lid = LicenseId(100)
        assert lid.value == 100
        assert LicenseId.identity_type is LicenseIdentity

    def test_license_sku(self):
        ls = LicenseSku("ENT-BWR-1G")
        assert ls.value == "ENT-BWR-1G"
        assert LicenseSku.identity_type is LicenseIdentity

    def test_license_name(self):
        ln = LicenseName("Enterprise 1G")
        assert ln.value == "Enterprise 1G"
        assert LicenseName.identity_type is LicenseIdentity

    def test_different_key_types_are_not_equal(self):
        """SegmentId(1) and EdgeId(1) are distinct dataclass instances."""
        sid = SegmentId(1)
        eid = EdgeId(1)
        assert sid != eid  # different types

    def test_same_key_type_equality(self):
        assert SegmentId(1) == SegmentId(1)
        assert SegmentId(1) != SegmentId(2)


class TestIdentityDataclasses:
    def test_segment_identity(self):
        u = uuid.uuid4()
        si = SegmentIdentity(
            name="Global Segment",
            segment_id=0,
            object_id=100,
            logical_id=u,
        )
        assert si.name == "Global Segment"
        assert si.segment_id == 0
        assert si.object_id == 100
        assert si.logical_id == u

    def test_edge_identity(self):
        u = uuid.uuid4()
        ei = EdgeIdentity(
            name="Branch-NYC",
            id=123,
            logical_id=u,
        )
        assert ei.name == "Branch-NYC"
        assert ei.id == 123
        assert ei.logical_id == u

    def test_application_identity(self):
        ai = ApplicationIdentity(name="Zoom", id=42)
        assert ai.name == "Zoom"
        assert ai.id == 42

    def test_profile_identity(self):
        pi = ProfileIdentity(name="Default Profile", id=10)
        assert pi.name == "Default Profile"
        assert pi.id == 10

    def test_address_group_identity(self):
        u = uuid.uuid4()
        agi = AddressGroupIdentity(
            name="RFC1918",
            id=5,
            logical_id=u,
        )
        assert agi.name == "RFC1918"
        assert agi.id == 5
        assert agi.logical_id == u

    def test_service_group_identity(self):
        u = uuid.uuid4()
        sgi = ServiceGroupIdentity(
            name="Web Ports",
            id=7,
            logical_id=u,
        )
        assert sgi.name == "Web Ports"
        assert sgi.id == 7
        assert sgi.logical_id == u

    def test_license_identity(self):
        li = LicenseIdentity(
            name="Enterprise 1G",
            id=100,
            license_id=200,
            sku="ENT-BWR-1G",
        )
        assert li.name == "Enterprise 1G"
        assert li.id == 100
        assert li.license_id == 200
        assert li.sku == "ENT-BWR-1G"

    def test_identity_is_frozen(self):
        si = SegmentIdentity(
            name="Global",
            segment_id=0,
            object_id=100,
            logical_id=uuid.uuid4(),
        )
        with pytest.raises(AttributeError):
            setattr(si, "segment_id", 1)
