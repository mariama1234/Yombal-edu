from rest_framework.pagination import PageNumberPagination


class Pagination(PageNumberPagination):
    """
    Pagination par défaut, avec `?page_size=` en option.

    Vingt lignes conviennent à une liste qu'on parcourt ; elles ne conviennent
    pas à une liste déroulante, qui doit proposer toutes les classes de
    l'année et non les vingt premières.
    """
    page_size_query_param = "page_size"
    max_page_size = 200
