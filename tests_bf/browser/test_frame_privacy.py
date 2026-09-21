from tests_bf.browser.fixtures.web_app import WebFixture
from PIL import Image
from tests_bf.browser.test_lifecycle import manager_for, register


def test_marked_private_content_in_child_frame_is_masked_in_real_png(tmp_path):
    store, manager, project = manager_for(tmp_path)
    with WebFixture() as site:
        register(manager, project, site)
        token = store.begin(project)['bf_task_id']
        try:
            session = manager.start(token, 'fixture', 'start')['result']['session']['session_id']
            page = manager.request(token, session, 'new_page', 'new', {})['result']['page_id']
            result = manager.request(token, session, 'navigate', 'nav',
                                     {'page_id': page, 'url': site.origin+'/iframe-private'})
            assert result['state'] == 'completed'
            image = manager.read(token, session, 'screenshot', {'page_id': page})
            with Image.open(image['path']) as png:
                assert png.convert('RGB').getpixel((40, 40)) == (255, 0, 255), 'child frame private area was not masked'
        finally:
            store.end(token)
            manager.shutdown()
