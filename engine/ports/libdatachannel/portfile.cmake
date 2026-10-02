vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO paullouisageneau/libdatachannel
    REF "v${VERSION}"
    SHA512 d08f4304899532847d06eec73db6ffc1cf55ae3b94b0d1da18cb3e77873bce556093d623298ecae0be7f5da82c1630138f7e911293375cb450ba5fc470b13cf7
    HEAD_REF master
    PATCHES
        dependencies.diff
        uwp-warnings.patch
)

vcpkg_check_features(OUT_FEATURE_OPTIONS FEATURE_OPTIONS
    FEATURES
        srtp USE_SRTP
)

vcpkg_cmake_configure(
    SOURCE_PATH "${SOURCE_PATH}"
    OPTIONS
        ${FEATURE_OPTIONS}
        -DUSE_SYSTEM_PLOG=ON
        -DUSE_SYSTEM_SRTP=ON
        -DUSE_JUICE=OFF
        -DUSE_NICE=ON
        -DNO_WEBSOCKET=OFF
        -DNO_MEDIA=OFF
        -DNO_EXAMPLES=ON
        -DNO_TESTS=ON
        -DCMAKE_DISABLE_FIND_PACKAGE_Git=ON
)

vcpkg_cmake_install()
vcpkg_cmake_config_fixup(PACKAGE_NAME LibDataChannel CONFIG_PATH lib/cmake/LibDataChannel)
vcpkg_fixup_pkgconfig()

file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/debug/include")
vcpkg_install_copyright(FILE_LIST "${SOURCE_PATH}/LICENSE")
