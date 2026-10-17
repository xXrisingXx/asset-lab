from harness import EditorTestCase


class ConfirmDialog(EditorTestCase):
    """confirmAction (app.js): each question gets its own answer, even when a second one is asked right away."""

    def test_a_quick_second_question_is_not_answered_by_the_first(self):
        b = self.browser
        # The first question is cancelled and the second asked before the first's close event has arrived (it is
        # dispatched later, as a task). The second must wait for its own answer.
        got = b.eval("""(async()=>{const d=document.getElementById('dialog');
            const first=confirmAction('First?','one','Yes');d.close('cancel');
            const second=confirmAction('Second?','two','Yes');
            const a=await first;await new Promise(r=>setTimeout(r,60));
            const stillAsking=d.open;d.close('ok');const c=await second;return [a,stillAsking,c];})()""")
        self.assertEqual(got, [False, True, True])

    def test_fonts_closed_while_loading_stays_closed(self):
        b = self.browser
        # Fonts opens at once with "Loading fonts…"; closed before the list arrives, it must not open again by itself.
        got = b.eval("""(async()=>{const d=document.getElementById('fontsDialog');const opening=openFonts();
            const shown=d.open;d.close();await opening;await new Promise(r=>setTimeout(r,300));return [shown,d.open];})()""")
        self.assertEqual(got, [True, False])
