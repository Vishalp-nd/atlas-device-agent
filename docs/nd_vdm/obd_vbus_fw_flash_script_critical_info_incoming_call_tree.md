# Incoming Call Tree for VBUS FW Flash Python Script Critical Info

## Scope

Representative script entries (same pattern per platform):
- scripts/krait/obd.vbus.fw.py
- scripts/krait2/obd.vbus.fw.py
- scripts/bagheera2/obd.vbus.fw.py
- scripts/bagheera3/obd.vbus.fw.py

Entry point:
- if __name__ == "__main__": main(deviceid, py_to_cpp_obj)

## Mermaid Flow

```mermaid
flowchart TD
  MAIN["main() - scripts/*/obd.vbus.fw.py"]
  MAIN --> I1["register_pyservicemon()"]
  MAIN --> F1["flash_post_data()"]
  MAIN --> C1["VBUS detection loop"]

  I1 --> N1["code=95999 aux=20 object created"]
  F1 --> N2["code=95999 aux=13 post finished"]
  F1 --> N3["code=95999 aux=10 status==200"]
  F1 --> N4["code=95999 aux=11 status!=200"]
  C1 --> N5["code=95999 aux=17 VBUS not found"]
```

## Deterministic Text Call Tree

### Wrapper and emit function

- obd.vbus.fw.py: `post_critical_event(py_to_cpp_obj, crit_msg, aux_code)`
- Calls: `py_service_mon.PY_Class_PY_TO_CPP.py_send_err_msg_to_cpp(py_to_cpp_obj, 95999, aux_code, byte_crit_msg)`

### Emission paths

- object creation: aux=20, "VBUS_FW_FLASH object created"
- post finished: aux=13
- upload success: aux=10
- upload fail HTTP status: aux=11
- latest FW already present: aux=11
- VBUS not found after retries: aux=17
