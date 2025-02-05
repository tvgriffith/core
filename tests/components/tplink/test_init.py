"""Tests for the TP-Link component."""
from __future__ import annotations

import copy
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant import setup
from homeassistant.components import tplink
from homeassistant.components.tplink.const import CONF_DEVICE_CONFIG, DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    CONF_AUTHENTICATION,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import EntityRegistry
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from . import (
    CREATE_ENTRY_DATA_AUTH,
    DEVICE_CONFIG_AUTH,
    IP_ADDRESS,
    MAC_ADDRESS,
    _mocked_dimmer,
    _patch_connect,
    _patch_discovery,
    _patch_single_discovery,
)

from tests.common import MockConfigEntry, async_fire_time_changed


async def test_configuring_tplink_causes_discovery(hass: HomeAssistant) -> None:
    """Test that specifying empty config does discovery."""
    with patch("homeassistant.components.tplink.Discover.discover") as discover, patch(
        "homeassistant.components.tplink.Discover.discover_single"
    ):
        discover.return_value = {MagicMock(): MagicMock()}
        await async_setup_component(hass, tplink.DOMAIN, {tplink.DOMAIN: {}})
        await hass.async_block_till_done()
        call_count = len(discover.mock_calls)
        assert discover.mock_calls

        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        assert len(discover.mock_calls) == call_count * 2

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=15))
        await hass.async_block_till_done()
        assert len(discover.mock_calls) == call_count * 3

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=30))
        await hass.async_block_till_done()
        assert len(discover.mock_calls) == call_count * 4


async def test_config_entry_reload(hass: HomeAssistant) -> None:
    """Test that a config entry can be reloaded."""
    already_migrated_config_entry = MockConfigEntry(
        domain=DOMAIN, data={}, unique_id=MAC_ADDRESS
    )
    already_migrated_config_entry.add_to_hass(hass)
    with _patch_discovery(), _patch_single_discovery(), _patch_connect():
        await async_setup_component(hass, tplink.DOMAIN, {tplink.DOMAIN: {}})
        await hass.async_block_till_done()
        assert already_migrated_config_entry.state == ConfigEntryState.LOADED
        await hass.config_entries.async_unload(already_migrated_config_entry.entry_id)
        await hass.async_block_till_done()
        assert already_migrated_config_entry.state == ConfigEntryState.NOT_LOADED


async def test_config_entry_retry(hass: HomeAssistant) -> None:
    """Test that a config entry can be retried."""
    already_migrated_config_entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: IP_ADDRESS}, unique_id=MAC_ADDRESS
    )
    already_migrated_config_entry.add_to_hass(hass)
    with _patch_discovery(no_device=True), _patch_single_discovery(
        no_device=True
    ), _patch_connect(no_device=True):
        await async_setup_component(hass, tplink.DOMAIN, {tplink.DOMAIN: {}})
        await hass.async_block_till_done()
        assert already_migrated_config_entry.state == ConfigEntryState.SETUP_RETRY


async def test_dimmer_switch_unique_id_fix_original_entity_still_exists(
    hass: HomeAssistant, entity_reg: EntityRegistry
) -> None:
    """Test no migration happens if the original entity id still exists."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=MAC_ADDRESS)
    config_entry.add_to_hass(hass)
    dimmer = _mocked_dimmer()
    rollout_unique_id = MAC_ADDRESS.replace(":", "").upper()
    original_unique_id = tplink.legacy_device_id(dimmer)
    original_dimmer_entity_reg = entity_reg.async_get_or_create(
        config_entry=config_entry,
        platform=DOMAIN,
        domain="light",
        unique_id=original_unique_id,
        original_name="Original dimmer",
    )
    rollout_dimmer_entity_reg = entity_reg.async_get_or_create(
        config_entry=config_entry,
        platform=DOMAIN,
        domain="light",
        unique_id=rollout_unique_id,
        original_name="Rollout dimmer",
    )

    with _patch_discovery(device=dimmer), _patch_single_discovery(
        device=dimmer
    ), _patch_connect(device=dimmer):
        await setup.async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    migrated_dimmer_entity_reg = entity_reg.async_get_or_create(
        config_entry=config_entry,
        platform=DOMAIN,
        domain="light",
        unique_id=original_unique_id,
        original_name="Migrated dimmer",
    )
    assert migrated_dimmer_entity_reg.entity_id == original_dimmer_entity_reg.entity_id
    assert migrated_dimmer_entity_reg.entity_id != rollout_dimmer_entity_reg.entity_id


async def test_config_entry_wrong_mac_Address(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test config entry enters setup retry when mac address mismatches."""
    mismatched_mac = f"{MAC_ADDRESS[:-1]}0"
    already_migrated_config_entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, unique_id=mismatched_mac
    )
    already_migrated_config_entry.add_to_hass(hass)
    with _patch_discovery(), _patch_single_discovery(), _patch_connect():
        await async_setup_component(hass, tplink.DOMAIN, {tplink.DOMAIN: {}})
        await hass.async_block_till_done()
        assert already_migrated_config_entry.state == ConfigEntryState.SETUP_RETRY

    assert (
        "Unexpected device found at 127.0.0.1; expected aa:bb:cc:dd:ee:f0, found aa:bb:cc:dd:ee:ff"
        in caplog.text
    )


async def test_config_entry_device_config(
    hass: HomeAssistant,
    mock_discovery: AsyncMock,
    mock_connect: AsyncMock,
) -> None:
    """Test that a config entry can be loaded with DeviceConfig."""
    mock_config_entry = MockConfigEntry(
        title="TPLink",
        domain=DOMAIN,
        data={**CREATE_ENTRY_DATA_AUTH},
        unique_id=MAC_ADDRESS,
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_config_entry_with_stored_credentials(
    hass: HomeAssistant,
    mock_discovery: AsyncMock,
    mock_connect: AsyncMock,
) -> None:
    """Test that a config entry can be loaded when stored credentials are set."""
    stored_credentials = tplink.Credentials("fake_username1", "fake_password1")
    mock_config_entry = MockConfigEntry(
        title="TPLink",
        domain=DOMAIN,
        data={**CREATE_ENTRY_DATA_AUTH},
        unique_id=MAC_ADDRESS,
    )
    auth = {
        CONF_USERNAME: stored_credentials.username,
        CONF_PASSWORD: stored_credentials.password,
    }

    hass.data.setdefault(DOMAIN, {})[CONF_AUTHENTICATION] = auth
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    config = DEVICE_CONFIG_AUTH
    assert config.credentials != stored_credentials
    config.credentials = stored_credentials
    mock_connect["connect"].assert_called_once_with(config=config)


async def test_config_entry_device_config_invalid(
    hass: HomeAssistant,
    mock_discovery: AsyncMock,
    mock_connect: AsyncMock,
    caplog,
) -> None:
    """Test that an invalid device config logs an error and loads the config entry."""
    entry_data = copy.deepcopy(CREATE_ENTRY_DATA_AUTH)
    entry_data[CONF_DEVICE_CONFIG] = {"foo": "bar"}
    mock_config_entry = MockConfigEntry(
        title="TPLink",
        domain=DOMAIN,
        data={**entry_data},
        unique_id=MAC_ADDRESS,
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert (
        f"Invalid connection type dict for {IP_ADDRESS}: {entry_data.get(CONF_DEVICE_CONFIG)}"
        in caplog.text
    )


@pytest.mark.parametrize(
    ("error_type", "entry_state", "reauth_flows"),
    [
        (tplink.AuthenticationException, ConfigEntryState.SETUP_ERROR, True),
        (tplink.SmartDeviceException, ConfigEntryState.SETUP_RETRY, False),
    ],
    ids=["invalid-auth", "unknown-error"],
)
async def test_config_entry_errors(
    hass: HomeAssistant,
    mock_discovery: AsyncMock,
    mock_connect: AsyncMock,
    error_type,
    entry_state,
    reauth_flows,
) -> None:
    """Test that device exceptions are handled correctly during init."""
    mock_connect["connect"].side_effect = error_type
    mock_config_entry = MockConfigEntry(
        title="TPLink",
        domain=DOMAIN,
        data={**CREATE_ENTRY_DATA_AUTH},
        unique_id=MAC_ADDRESS,
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is entry_state
    assert (
        any(mock_config_entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
        == reauth_flows
    )
