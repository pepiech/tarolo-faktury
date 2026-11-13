class ConcurrentUpdate(Exception):
    """Záznam mezitím změnil někdo jiný (optimistický zámek)."""

    message = "Záznam mezitím změnil jiný uživatel. Načtěte prosím aktuální data a změnu zopakujte."


def lock_and_check(queryset, pk, expected_version):
    """Zamkne řádek (v transakci) a ověří, že se od načtení nezměnila verze."""
    obj = queryset.select_for_update(of=("self",)).get(pk=pk)
    if expected_version is not None and int(obj.version) != int(expected_version):
        raise ConcurrentUpdate()
    return obj


def bump(obj):
    obj.version = obj.version + 1
    return obj
