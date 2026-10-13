"""The diagnostic revision must represent the descriptor actually sent to clients."""

from copy import deepcopy

import pytest

from personal_mcp.catalog import catalog_revision, definition


def sample_catalog():
    return {'observe': definition('observe', 'Read owned state without changing it.',
        {}, [], {'type': 'object'}, readonly=True)}


@pytest.mark.parametrize('field,value', [
    ('description', 'Changed effects, permissions or confirmation requirements.'),
    ('title', 'Owned workflow state'),
    ('_meta', {'ui': {'visibility': ['model']}}),
    ('execution', {'taskSupport': 'forbidden'}),
])
def test_revision_changes_when_any_published_descriptor_changes(field, value):
    catalog = sample_catalog()
    original = catalog_revision(catalog)
    changed = deepcopy(catalog)
    changed['observe'][field] = value
    assert catalog_revision(changed) != original
    assert catalog_revision(catalog) == original


def test_revision_is_independent_of_dictionary_insertion_order():
    first = sample_catalog()
    first['other'] = definition('other', 'Other read.', {}, [], {'type': 'object'}, readonly=True)
    reversed_catalog = {name: dict(reversed(list(tool.items())))
                        for name, tool in reversed(list(first.items()))}
    assert catalog_revision(first) == catalog_revision(reversed_catalog)
