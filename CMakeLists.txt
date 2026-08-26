cmake_minimum_required(VERSION 3.16)

project(OOFEMSalomePlugin VERSION 1.0.0 LANGUAGES NONE)

set(
  OOFEM_INSTALL_PYTHON_DIR
  "bin/salome"
  CACHE PATH "SALOME Python module directory relative to CMAKE_INSTALL_PREFIX"
)
set(
  OOFEM_INSTALL_RESOURCE_DIR
  "share/salome/resources/oofem"
  CACHE PATH "SALOME OOFEM resource directory relative to CMAKE_INSTALL_PREFIX"
)
set(
  OOFEM_INSTALL_ENV_HOOK_DIR
  ""
  CACHE PATH "Optional absolute SALOME extra.env.d directory"
)

install(
  DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/src/OOFEMSalomePlugin"
  DESTINATION "${OOFEM_INSTALL_PYTHON_DIR}"
  PATTERN "__pycache__" EXCLUDE
  PATTERN "*.pyc" EXCLUDE
  PATTERN "*.orig" EXCLUDE
  PATTERN "*.rej" EXCLUDE
)

install(
  FILES
    module/OOFEMGUI.py
    module/oofem_preferences.py
    module/register_oofem_user_config.py
    module/oofem_env.py
  DESTINATION "${OOFEM_INSTALL_PYTHON_DIR}"
)

install(
  FILES module/SalomeApp.xml module/oofem.png module/oofem-logo.png
  DESTINATION "${OOFEM_INSTALL_RESOURCE_DIR}"
)

if(OOFEM_INSTALL_ENV_HOOK_DIR)
  install(
    FILES module/oofem_env.py
    DESTINATION "${OOFEM_INSTALL_ENV_HOOK_DIR}"
    RENAME oofem.py
  )
endif()
