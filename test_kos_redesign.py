"""Regression checks for the user-facing kos marketplace redesign."""

import pytest

import app_user


@pytest.fixture()
def authenticated_client(tmp_path, monkeypatch):
    # Isolate the shared maintenance flag so unrelated tests cannot leak state here.
    monkeypatch.setattr(app_user, "SITE_CONFIG_PATH", tmp_path / "site_config.json")
    app_user.app.config.update(TESTING=True)
    with app_user.app.test_client() as client:
        with client.session_transaction() as session:
            session["user"] = "kos-redesign-qa@example.test"
            session["user_id"] = 99999
            session["user_name"] = "QA Kos"
            session["theme"] = "light"
            session["font_size"] = "medium"
        yield client


def test_kos_route_renders_marketplace_and_only_named_listings(authenticated_client):
    response = authenticated_client.get("/category/kos")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Temukan kos yang cocok untuk aktivitasmu di Malang" in html
    assert "SmartKost" in html
    assert "Kost Putri Ambarawa No. 23" in html
    assert "Kos putri siguragura near ub" in html
    assert "Tanpa Nama" not in html
    assert "baris data belum memiliki nama kos" in html


def test_kos_search_filters_records_on_the_server(authenticated_client):
    response = authenticated_client.get("/category/kos?q=SmartKost")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "SmartKost" in html
    assert "Kost Putri Ambarawa No. 23" not in html
    assert "Tidak menemukan kos yang cocok" not in html


def test_type_filter_only_uses_observed_values(authenticated_client):
    response = authenticated_client.get("/category/kos?tipe=Putri")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "SmartKost" in html
    assert "Kost Putri Ambarawa No. 23" in html
    assert "Kos putri siguragura near ub" not in html


def test_missing_prices_are_not_fabricated_and_price_filter_is_applied(authenticated_client):
    normal_html = authenticated_client.get("/category/kos").get_data(as_text=True)
    filtered_html = authenticated_client.get("/category/kos?min_harga=500000").get_data(as_text=True)

    assert "Harga belum tercantum" in normal_html
    assert "Rp0" not in normal_html
    assert "Tidak menemukan kos yang cocok" in filtered_html
    assert "Rp0" not in filtered_html


def test_invalid_price_range_is_handled_without_server_error(authenticated_client):
    response = authenticated_client.get("/category/kos?min_harga=1000000&max_harga=500000")
    assert response.status_code == 200
    assert "Filter harga direset" in response.get_data(as_text=True)


def test_detail_routes_render_correct_record_and_reject_unidentified_rows(authenticated_client):
    query_detail = authenticated_client.get("/detail?category=kos&item_id=0")
    path_detail = authenticated_client.get("/detail/kos/0")

    assert query_detail.status_code == 200
    assert path_detail.status_code == 200
    assert "SmartKost" in query_detail.get_data(as_text=True)
    assert "SmartKost" in path_detail.get_data(as_text=True)
    assert authenticated_client.get("/detail/kos/3").status_code == 404
    assert authenticated_client.get("/detail/kos/9999").status_code == 404


def test_non_kos_categories_keep_the_generic_template(authenticated_client):
    response = authenticated_client.get("/category/warung")
    assert response.status_code == 200
    assert "Tempat Makan" in response.get_data(as_text=True)


def test_external_link_validation_rejects_bad_contact_and_maps_hosts():
    assert app_user.normalise_whatsapp_url("85791015124") == "https://wa.me/6285791015124"
    assert app_user.normalise_whatsapp_url("-7.959448339557285, 112.61549091349228") is None
    assert app_user.safe_google_maps_url("https://maps.app.goo.gl/example") == "https://maps.app.goo.gl/example"
    assert app_user.safe_google_maps_url("https://google.com.attacker.invalid/maps") is None


def test_dataset_read_error_is_distinguished_from_no_matches(authenticated_client, monkeypatch):
    def broken_dataset(*args, **kwargs):
        raise ValueError("CSV malformed")

    monkeypatch.setattr(app_user, "build_category_records", broken_dataset)
    response = authenticated_client.get("/category/kos?q=not-found")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Data kos belum dapat dimuat" in html
    assert "Tidak menemukan kos yang cocok" not in html
