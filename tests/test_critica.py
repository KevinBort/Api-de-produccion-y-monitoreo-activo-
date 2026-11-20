"""La regla que decide qué tareas piden aprobación humana."""

import pytest

from app.hitl import es_critica

CRITICAS = [
    "Envía el informe de opiniones al consejo coordinador",
    "manda un resumen de las cuotas a los socios",
    "Notifica a los socios el cambio de cuota",
    "Publica el análisis de reseñas en la cartelera",
    "Calcula el sentimiento y envíalo al consejo",
]
NORMALES = [   # las mismas que usa scripts/load_test.py: NO deben pausarse
    "Investiga cuánto cuesta la cuota para un socio jubilado y calcula cuánto pagaría en un año.",
    "¿Qué opinan los socios sobre las cuotas? Dime el porcentaje de opiniones positivas.",
    "Resume los requisitos para ser socio y qué dicen las reseñas sobre los talleres.",
    "¿Qué sanciones prevé el reglamento y qué opinan los socios sobre la publicación de normas?",
    "Calcula el porcentaje de opiniones positivas sobre el compostaje.",
]


@pytest.mark.parametrize("q", CRITICAS)
def test_detecta_acciones_con_efecto(q):
    assert es_critica(q)


@pytest.mark.parametrize("q", NORMALES)
def test_consultas_de_solo_lectura_no_son_criticas(q):
    assert not es_critica(q)


def test_el_flag_manda_sobre_la_regla():
    assert es_critica("¿Cuánto cuesta la cuota?", override=True)
    assert not es_critica("Envía el informe al consejo", override=False)
