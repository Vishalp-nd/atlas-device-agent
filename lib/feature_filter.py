"""
Run only the devices that have every requested feature enabled (the run form's feature filter).

Before any log is downloaded, each device's config is put together the way the device builds it:
  - the cloud override config from IDMS (cloud_config: the same fetch as the report's cloud
    override config check), which holds only the keys that differ from the defaults;
  - the OTA package defaults (bagheera_config.ini + nd_config.ini in INPUT/<ota>/, downloaded
    by ota_report.ensure_ini when missing) for every other key.
Each feature of INPUT/Feature_Mapping/feature_mapping.ini is then evaluated with
feature_status.evaluate, the cloud values taking the place of values read from the logs.
A device is kept when every requested feature is ON; OFF and NA (a needed key in neither the
defaults nor the override) skip it, and so does a device whose config can't be put together
(cloud fetch failed, OTA unknown, OTA package config missing).

Which OTA's defaults: an OTA run's OTA; for a device list, the OTA the device is on now
(c_p_version of its IDMS record, the version the cloud override applies to), or the run's
only OTA when the record has none.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import cloud_config
import feature_status

log = logging.getLogger('feature_filter')

ON = 'ON'


def feature_names():
    """The features the form can pick from, in feature_mapping.ini order."""
    return [f['name'] for f in feature_status.load_features()]


def selected_features(names):
    """[{'name', 'conditions'}] for names; ValueError listing the unknown ones."""
    by_name = {f['name']: f for f in feature_status.load_features()}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(f"Unknown feature(s): {', '.join(unknown)} (see feature_mapping.ini)")
    return [by_name[n] for n in dict.fromkeys(names)]


def _fetch(cloud, device):
    """(override config {section: {key: value}} or {}, IDMS c_p_version or None, error or None)."""
    try:
        data = cloud.device(device)
    except Exception as exc:  # one device's failure does not stop the others
        log.warning('%s: cloud override config not fetched: %s', device, exc)
        return None, None, f'Cloud fetch failed: {type(exc).__name__}: {exc}'[:300]
    version = ((data.get('device') or {}).get('c_p_version') or '').strip() or None
    return cloud_config.override_config(data) or {}, version, None


def _defaults(ota, product, cache):
    """(the OTA package's {section: {key: value}} or None, why it is None), downloaded if needed."""
    if ota not in cache:
        import ota_report  # heavy import, only when the filter runs

        try:
            _, error = ota_report.ensure_ini(ota, product)
            values = ota_report.ota_config_values(ota)
        except Exception as exc:  # the other OTAs' devices are still checked
            values, error = None, f'{type(exc).__name__}: {exc}'
        if values is None:
            log.warning('%s: OTA package config unavailable: %s', ota, error)
        cache[ota] = (values, error)
    return cache[ota]


def filter_devices(devices, names, environment, run_ota=None, run_otas=None, product=None, workers=None):
    """Check devices against the features named; returns
    {'features', 'checked', 'kept': [device], 'skipped': {device: reason},
     'devices': {device: {'ota', 'overrides', 'features': {name: status}, 'kept', 'reason'}}}.

    run_ota: an OTA run's OTA (its defaults are used for every device); run_otas: a device
    run's OTA list (its only OTA is used when IDMS has no version for a device).
    Raises RuntimeError when IDMS can't be used at all (no credentials, login refused).
    """
    features = selected_features(names)
    devices = list(dict.fromkeys(devices))
    result = {'features': [f['name'] for f in features], 'checked': len(devices), 'kept': [], 'skipped': {},
              'devices': {}}
    if not devices:
        return result
    if not cloud_config.has_credentials(environment):
        raise RuntimeError('The feature filter needs the IDMS login (IDMS_USERNAME / IDMS_PASSWORD'
                           + (', or IDMS_PROD_USERNAME / IDMS_PROD_PASSWORD' if environment == 'prod' else '')
                           + ') in .env')
    cloud = cloud_config.Cloud(environment)
    try:
        cloud.login()
    except Exception as exc:
        raise RuntimeError(f'Feature filter: IDMS login ({cloud.auth_url}) failed: {type(exc).__name__}: {exc}') from exc
    with ThreadPoolExecutor(min(workers or cloud_config.FETCH_WORKERS, len(devices))) as pool:
        fetched = dict(zip(devices, pool.map(lambda d: _fetch(cloud, d), devices)))

    fallback_ota = run_ota or (run_otas[0] if run_otas and len(run_otas) == 1 else None)
    defaults_cache = {}
    for device in devices:
        overrides, version, error = fetched[device]
        ota = run_ota or version or fallback_ota
        entry = {'ota': ota, 'overrides': None, 'features': {}, 'kept': False, 'reason': None}
        if error:
            entry['reason'] = error
        elif not ota:
            entry['reason'] = 'OTA unknown (no c_p_version in IDMS)'
        else:
            defaults, why = _defaults(ota, product, defaults_cache)
            if defaults is None:
                entry['reason'] = (f'OTA {ota} package config (bagheera_config.ini / nd_config.ini) not available'
                                   + (f': {why}' if why else ''))[:300]
            else:
                entry['overrides'] = sum(len(keys) for keys in overrides.values())
                values = {f'{s}.{k}': {'value': v} for s, keys in overrides.items() for k, v in keys.items()}
                statuses = feature_status.evaluate(features, defaults, set(), values)
                entry['features'] = {f['name']: status for f, (status, _) in zip(features, statuses)}
                off = [f"{name} ({'off' if status == 'OFF' else 'a key is not in the config'})"
                       for name, status in entry['features'].items() if status != ON]
                entry['kept'] = not off
                entry['reason'] = None if not off else 'Not enabled: ' + ', '.join(off)
        result['devices'][device] = entry
        if entry['kept']:
            result['kept'].append(device)
        else:
            result['skipped'][device] = entry['reason']
    log.info('Feature filter %s: %d of %d device(s) have them all enabled (cloud override + OTA defaults)',
             result['features'], len(result['kept']), len(devices))
    return result
