from dax.sim import enable_dax_sim

device_db = {
    "core": {
        "type": "local",
        "module": "artiq.coredevice.core",
        "class": "Core",
        "arguments": {
            "host": "127.0.0.1",
            "ref_period": 1e-9
        }
    },
    "ttl_aom_sw": {
        "type": "local",
        "module": "artiq.coredevice.ttl",
        "class": "TTLOut",
        "arguments": {
            "channel": 0
        }
    }
}

enable_dax_sim(device_db)
