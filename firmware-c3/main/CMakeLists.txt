idf_component_register(SRCS "main.c" "vista.c" "pantalla.c" "fuentes.c" "protocolo.c" "lora.c" "almacen.c" "enlace.c" "portal.c" "consola.c"
                       INCLUDE_DIRS "."
                       EMBED_FILES "${CMAKE_CURRENT_BINARY_DIR}/index.html.gz")

# La página va comprimida dentro del firmware (se regenera sola si cambia web/index.html)
add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/index.html.gz"
    COMMAND ${PYTHON} -c "import gzip,sys; open(sys.argv[2],'wb').write(gzip.compress(open(sys.argv[1],'rb').read(),9,mtime=0))"
            "${CMAKE_CURRENT_LIST_DIR}/../web/index.html" "${CMAKE_CURRENT_BINARY_DIR}/index.html.gz"
    DEPENDS "${CMAKE_CURRENT_LIST_DIR}/../web/index.html"
    VERBATIM)
add_custom_target(pagina_web DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/index.html.gz")
add_dependencies(${COMPONENT_LIB} pagina_web)
