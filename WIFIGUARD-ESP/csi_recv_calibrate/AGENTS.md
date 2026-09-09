# AI Agent Guide for CSI Receiver Calibration Project

## Purpose
This is an ESP-IDF application project for CSI receive calibration on ESP32-C5 family chips. The main application lives in `main/app_main.c`, and the project uses ESP-IDF CMake build conventions with managed third-party components under `managed_components/`.

## Key files and structure
- `CMakeLists.txt` — root ESP-IDF project entrypoint.
- `main/CMakeLists.txt` — registers the main application component.
- `main/app_main.c` — primary firmware logic, including Wi-Fi, ESP-NOW, and CSI pipeline.
- `sdkconfig` / `sdkconfig.defaults` — build configuration for the target.
- `managed_components/espressif__esp_csi_gain_ctrl/` — vendored CSI gain control component.
- `managed_components/espressif__cmake_utilities/` — vendored utility helpers for CMake and versioning.

## Recommended commands
Use the ESP-IDF environment before running these commands.
- `idf.py build`
- `idf.py flash`
- `idf.py monitor`
- `idf.py menuconfig` for changing project configuration

If the development environment is not already set to the correct target, use the standard ESP-IDF setup commands before build.

## Development conventions
- Keep app logic inside `main/` and treat `managed_components/` as vendored dependencies unless a component bug requires a patch.
- Preserve the root CMake ordering and ESP-IDF boilerplate in `CMakeLists.txt`.
- Use `idf_component_register` in `main/CMakeLists.txt` and do not duplicate ESP-IDF component registration patterns.
- Update `sdkconfig` through `idf.py menuconfig` rather than manually editing unless a specific setting must be changed.

## What agents should do first
- Inspect `main/app_main.c` for any firmware change requests.
- Verify build changes by reasoning through `idf.py build` and the ESP-IDF target / component setup.
- Avoid making assumptions about hardware-specific CSI or ESP-NOW behavior beyond what the code and comments state.

## Notes for future work
- There is no dedicated test harness in the repository root; build and run on hardware are the primary validation methods.
- If additional project documentation is needed, refer to upstream ESP-IDF docs at https://docs.espressif.com/projects/esp-idf.
- For component-specific behavior, inspect `managed_components/espressif__esp_csi_gain_ctrl/README.md` and `managed_components/espressif__cmake_utilities/README.md`.
