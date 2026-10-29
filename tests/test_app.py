"""Opt-in desktop regression checks: RUNESPEAK_GUI_TESTS=1 python -m unittest discover -s tests."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('RUNESPEAK_GUI_TESTS') == '1', 'requires a desktop display')
class AppTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from runespeak.app import App
        self.root = tk.Tk(className='RunespeakLite')
        self.app = App(self.root, enable_bridge=False)
        self.root.update()

    def tearDown(self):
        self.app.close()

    def test_editor_visible_on_first_layout(self):
        self.assertTrue(self.app.text.winfo_ismapped())
        self.assertGreaterEqual(self.app.text.winfo_height(), 60)

    def test_controls_visible_at_minimum_window_size(self):
        self.root.geometry('620x460')
        self.root.update()
        self.assertGreaterEqual(self.app.text.winfo_height(), 60)
        self.assertTrue(self.app.text.winfo_ismapped())
        for name, button in self.app.buttons.items():
            self.assertTrue(button.winfo_ismapped(), name)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height(), name)
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width(), name)

    def test_play_generates_when_no_audio_exists(self):
        self.app.text.insert('1.0', 'Read this text.')
        with patch.object(self.app, 'narrate') as narrate:
            self.app.play()
        narrate.assert_called_once()

    def test_completed_generation_starts_playback(self):
        self.app.set_busy(True)
        audio = Path(self.app.temp.name) / 'ready.wav'
        self.app.events.put(('audio', audio))
        with patch.object(self.app, 'play') as play:
            self.app.poll()
        play.assert_called_once()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.audio, audio)
        self.assertFalse(self.app.buttons['Save WAV'].instate(['disabled']))

    def test_first_section_plays_while_generation_is_busy(self):
        from runespeak.streaming import SectionQueue
        self.app.stream = SectionQueue()
        self.app.set_busy(True)
        first = Path(self.app.temp.name) / 'first.wav'
        self.app.events.put(('section', (first, 1, 10)))
        with patch.object(self.app, 'start_file') as play:
            self.app.poll()
        play.assert_called_once_with(first)
        self.assertTrue(self.app.busy)
        self.assertEqual(self.app.stream.cursor, 1)

    def test_pause_between_sections_holds_new_audio(self):
        from runespeak.streaming import SectionQueue
        self.app.stream = SectionQueue()
        self.app.set_busy(True)
        self.app.pause()
        first = Path(self.app.temp.name) / 'first.wav'
        self.app.events.put(('section', (first, 1, 10)))
        with patch.object(self.app, 'start_file') as play:
            self.app.poll()
            play.assert_not_called()
            self.app.play()
        play.assert_called_once_with(first)

    def test_completed_stream_does_not_replay_combined_file(self):
        from runespeak.streaming import SectionQueue
        self.app.stream = SectionQueue(parts=[Path('first.wav')], cursor=1)
        self.app.set_busy(True)
        self.app.events.put(('audio', Path(self.app.temp.name) / 'complete.wav'))
        with patch.object(self.app, 'start_file') as play:
            self.app.poll()
        play.assert_not_called()
        self.assertTrue(self.app.stream.complete)
        self.assertFalse(self.app.buttons['Save WAV'].instate(['disabled']))

    def test_external_selection_goes_directly_to_narration(self):
        with patch.object(self.app, 'narrate') as narrate:
            result = self.app.handle_command({'action': 'read', 'text': 'Selected text', 'source': 'https://example.com/'})
        self.assertTrue(result['ok'])
        self.assertEqual(self.app.text.get('1.0', 'end').strip(), 'Selected text')
        self.assertEqual(self.app.url.get(), 'https://example.com/')
        narrate.assert_called_once()

    def test_busy_external_request_is_rejected(self):
        self.app.set_busy(True)
        with patch.object(self.app, 'narrate') as narrate:
            result = self.app.handle_command({'action': 'read', 'text': 'Another selection'})
        self.assertFalse(result['ok'])
        narrate.assert_not_called()

    def test_stop_ignores_sections_arriving_after_cancellation(self):
        from runespeak.streaming import SectionQueue
        self.app.stream = SectionQueue()
        self.app.set_busy(True)
        self.app.stop()
        self.app.events.put(('section', (Path('late.wav'), 1, 10)))
        with patch.object(self.app, 'start_file') as play:
            self.app.poll()
        play.assert_not_called()
        self.assertIsNone(self.app.stream)

    def test_clipboard_command_reads_without_paste_or_focus_change(self):
        with patch.object(self.root, 'clipboard_get', return_value='Copied selection'), patch.object(self.app, 'narrate') as narrate:
            result = self.app.handle_command({'action': 'clipboard'})
        self.assertTrue(result['ok'])
        self.assertEqual(self.app.text.get('1.0', 'end').strip(), 'Copied selection')
        narrate.assert_called_once()

    def test_header_text_never_overlaps_at_larger_font_sizes(self):
        from types import SimpleNamespace
        self.root.geometry('620x700')
        self.root.update()
        original = self.app.surface.size
        self.app.surface.size = lambda pixels: original(pixels) * 2
        self.app.surface.redraw(SimpleNamespace(width=620, height=700))
        boxes = [self.app.surface.bbox(item) for item in self.app.surface.header_items]
        self.assertLess(boxes[0][3], boxes[1][1])
        self.assertLess(boxes[1][3], boxes[2][1])
        self.assertLess(boxes[2][3], self.app.surface.coords(self.app.surface.window)[1])

    def test_load_saved_wav_sets_exact_duration(self):
        import wave
        path = Path(self.app.temp.name) / 'saved.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 48000)
        self.app.load_audio(path)
        self.assertEqual(self.app.total_seconds, 2)
        self.assertFalse(self.app.total_estimated)
        with self.assertRaises(ValueError):
            self.app.load_audio(path.with_name('missing.wav'))

    def test_save_defaults_to_music_and_recording_url(self):
        import json
        import tempfile
        self.app.audio = Path(self.app.temp.name) / 'completed.wav'
        self.app.audio.write_bytes(b'audio fixture')
        self.app.audio.with_suffix('.json').write_text(json.dumps({'source': 'https://distill.pub/2020/circuits/zoom-in/', 'text': 'Original recording'}))
        self.app.url.set('https://other.example/edited-after-generation')
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'Runespeak'
            with patch('runespeak.app.save_directory', return_value=directory), patch('runespeak.app.filedialog.asksaveasfilename', return_value='') as dialog:
                self.app.save()
            self.assertEqual(dialog.call_args.kwargs['initialdir'], str(directory))
            self.assertEqual(dialog.call_args.kwargs['initialfile'], 'distill-pub-2020-circuits-zoom-in.wav')
            self.assertTrue(directory.is_dir())

    def test_document_drop_preserves_spaces_in_filenames(self):
        from types import SimpleNamespace
        data = self.root.tk.call('list', '/tmp/my research.pdf', '/tmp/notes.txt')
        # Event payloads are Tcl list strings, not Python tuples.
        data = self.root.tk.call('format', '%s', data)
        with patch.object(self.app, 'load_documents') as load:
            self.assertEqual(self.app.drop_document(SimpleNamespace(data=data)), 'copy')
        load.assert_called_once_with(['/tmp/my research.pdf', '/tmp/notes.txt'])

    def test_document_link_drop_and_busy_drop(self):
        from types import SimpleNamespace
        with patch.object(self.app, 'load_documents') as load:
            self.app.drop_document(SimpleNamespace(data='https://arxiv.org/pdf/2507.11473'))
            load.assert_called_once_with(['https://arxiv.org/pdf/2507.11473'])
        self.app.set_busy(True)
        with patch.object(self.app, 'load_documents') as load:
            self.assertEqual(self.app.drop_document(SimpleNamespace(data='/tmp/paper.pdf')), 'none')
            load.assert_not_called()

    def test_native_document_drop_registration_is_active(self):
        self.assertTrue(self.app.dnd_enabled)
        self.assertTrue(self.root.tk.call('package', 'present', 'tkdnd'))
        self.assertTrue(self.app.text.dnd_bind('<<Drop>>'))

    def test_narration_cleanup_is_used_before_synthesis(self):
        self.app.text.insert('1.0', 'Article body. See https://example.org/long/path.\n\nReferences\nA long bibliography.')
        with patch('runespeak.app.chunks', return_value=[]) as split:
            self.app.narrate()
        spoken = split.call_args.args[0]
        self.assertIn('there is a URL here', spoken)
        self.assertNotIn('long bibliography', spoken)
        self.assertIn('A long bibliography', self.app.text.get('1.0', 'end'))
        self.app.include_backmatter.set(True)
        with patch('runespeak.app.chunks', return_value=[]) as split:
            self.app.narrate()
        self.assertIn('A long bibliography.', split.call_args.args[0])

    def test_transport_uses_icons_without_duplicate_read_aloud_button(self):
        self.assertNotIn('Read aloud', self.app.buttons)
        for key in ('Play / Resume', 'Pause', 'Stop', 'Restart'):
            self.assertTrue(self.app.buttons[key].cget('image'))
            self.assertTrue(self.app.buttons[key].cget('text'))
            self.assertEqual(str(self.app.buttons[key].cget('compound')), 'image')

    def test_timer_only_displays_one_elapsed_total_pair(self):
        self.app.total_seconds = 120
        self.app.clock.position = 15
        self.app.clock.session = 90
        self.app.poll()
        self.assertEqual(self.app.timer_text.get(), '0:15 / 2:00')
        self.assertNotIn('remaining', self.app.timer_text.get())
        self.assertNotIn('sitting', self.app.timer_text.get())

    def test_play_regenerates_after_text_changes(self):
        self.app.recorded_input = ('Old passage', False)
        self.app.text.insert('1.0', 'New passage')
        with patch.object(self.app, 'narrate') as narrate:
            self.app.play()
        narrate.assert_called_once()

    def test_read_along_highlights_current_audio_section(self):
        from runespeak.streaming import SectionQueue
        self.app.prepare_follow(['First passage.', 'Second passage.'])
        self.app.stream = SectionQueue(cursor=1)
        self.app.player = object()
        try:
            self.app.update_follow()
            ranges = self.app.reader.tag_ranges('speaking')
            self.assertEqual(self.app.reader.get(*ranges), 'First passage.')
            self.app.stream.cursor = 2
            self.app.update_follow()
            self.assertEqual(self.app.reader.get(*self.app.reader.tag_ranges('speaking')), 'Second passage.')
            self.app.paused = True
            self.app.set_highlight_color('#c3d1a7')
            self.assertEqual(self.app.reader.tag_cget('speaking', 'background'), '#c3d1a7')
            self.app.highlight_enabled.set(False)
            self.app.refresh_follow()
            self.assertFalse(self.app.reader.tag_ranges('speaking'))
        finally:
            self.app.player = None

    def test_follow_can_be_disabled_without_losing_highlight(self):
        from runespeak.streaming import SectionQueue
        self.app.prepare_follow(['A passage to read.'])
        self.app.stream = SectionQueue(cursor=1)
        self.app.follow_scroll.set(False)
        self.app.player = object()
        try:
            with patch.object(self.app.reader, 'yview') as scroll:
                self.app.update_follow()
            scroll.assert_not_called()
            self.assertTrue(self.app.reader.tag_ranges('speaking'))
            self.app.clear_follow()
            self.assertFalse(self.app.reader.tag_ranges('speaking'))
        finally:
            self.app.player = None

    def test_saved_narration_restores_section_following(self):
        import json
        import wave
        path = Path(self.app.temp.name) / 'saved.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 48000)
        path.with_suffix('.json').write_text(json.dumps({'sections': [{'text': 'One.', 'duration': 1}, {'text': 'Two.', 'duration': 1}]}))
        self.app.load_audio(path)
        self.assertEqual(self.app.follow_durations, [1, 1])
        self.assertEqual(len(self.app.follow_ranges), 2)
        self.assertIn('One.\n\nTwo.', self.app.reader.get('1.0', 'end'))

    def test_voice_selection_is_persisted_without_restarting_audio(self):
        self.app.voice.set('Easton')
        with patch('runespeak.app.save_preferences') as save, patch.object(self.app, 'narrate') as narrate:
            self.app.select_narrator()
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0]['voice'], 'Easton')
        narrate.assert_not_called()

    def test_changed_voice_regenerates_on_next_play(self):
        self.app.text.insert('1.0', 'A passage.')
        self.app.recorded_input = ('A passage.', False, 'default')
        self.app.voice.set('Freya')
        with patch.object(self.app, 'narrate') as narrate:
            self.app.play()
        narrate.assert_called_once()

    def test_preview_does_not_start_during_narration(self):
        self.app.set_busy(True)
        with patch('runespeak.app.subprocess.Popen') as process:
            self.app.preview_narrator()
        process.assert_not_called()

    def test_read_from_selected_source_keeps_original_document(self):
        self.app.text.insert('1.0', 'Opening.\n\nStart from this paragraph. Continue here.')
        self.app.pages.select(0)
        self.app.text.tag_add('sel', '3.0', '3.5')
        with patch.object(self.app, 'request_reading') as request:
            self.app.read_from_here()
        request.assert_called_once_with('Start from this paragraph. Continue here.')
        self.assertTrue(self.app.text.get('1.0','end').startswith('Opening.'))

    def test_read_from_here_cancels_then_defers_until_worker_is_idle(self):
        self.app.set_busy(True)
        with patch.object(self.app, 'narrate') as narrate:
            self.app.request_reading('Selected suffix.')
            narrate.assert_not_called()
            self.app.events.put(('idle', None))
            self.app.poll()
        narrate.assert_called_once_with(text_override='Selected suffix.')

    def test_seek_crosses_generated_passages_and_preserves_pause(self):
        from runespeak.streaming import SectionQueue
        self.app.stream = SectionQueue(parts=[Path('a.wav'),Path('b.wav'),Path('c.wav')], cursor=1)
        self.app.section_durations = [5,10,10]
        self.app.clock.position = 2
        self.app.paused = True
        with patch.object(self.app,'start_file') as start, patch.object(self.app,'pause') as pause:
            self.app.seek_relative(10)
        start.assert_called_once_with(Path('b.wav'), seek=7)
        pause.assert_called_once()
        self.assertEqual(self.app.stream.cursor,2)

    def test_forward_seek_clamps_to_generated_audio(self):
        from runespeak.streaming import SectionQueue
        self.app.stream = SectionQueue(parts=[Path('a.wav')], cursor=1)
        self.app.section_durations = [4]
        self.app.clock.position = 2
        self.app.set_busy(True)
        with patch.object(self.app,'start_file') as start:
            self.app.seek_relative(10)
        start.assert_not_called()
        self.assertEqual(self.app.clock.position,4)
        self.assertIn('generated audio', self.app.status.get())

    def test_highlighting_after_jump_uses_full_document_offset(self):
        from runespeak.streaming import SectionQueue
        self.app.prepare_follow(['Earlier words.', 'Selected section.', 'Later words.'])
        self.app.playback_passage_offset = 1
        self.app.stream = SectionQueue(cursor=1)
        self.app.player = object()
        try:
            self.app.update_follow()
            self.assertEqual(self.app.reader.get(*self.app.reader.tag_ranges('speaking')), 'Selected section.')
            self.assertIn('Earlier words.', self.app.reader.get('1.0','end'))
        finally:
            self.app.player = None

    def test_narration_jump_preserves_readalong_and_allows_return(self):
        full = 'Earlier words remain. Selected passage starts. Later words remain.'
        self.app.text.insert('1.0', full)
        with patch('runespeak.app.threading.Thread'):
            self.app.narrate(text_override='Selected passage starts. Later words remain.')
        self.assertIn('Earlier words remain.', self.app.reader.get('1.0', 'end'))
        self.assertGreater(self.app.playback_passage_offset, 0)
        self.app.set_busy(False)
        with patch('runespeak.app.threading.Thread'):
            self.app.narrate(text_override=full)
        self.assertEqual(self.app.playback_passage_offset, 0)
        self.assertEqual(self.app.text.get('1.0', 'end').strip(), full)

    def test_reopened_jump_retains_prior_context(self):
        import json
        import wave
        path = Path(self.app.temp.name) / 'jump.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 24000)
        path.with_suffix('.json').write_text(json.dumps({
            'context_text': 'Earlier words. Selected words.',
            'read_along': ['Earlier words.', 'Selected words.'],
            'playback_passage_offset': 1,
            'sections': [{'text': 'Selected words.', 'duration': 1}]}))
        self.app.load_audio(path)
        self.assertEqual(self.app.playback_passage_offset, 1)
        self.assertIn('Earlier words.', self.app.reader.get('1.0', 'end'))
        self.assertIn('Earlier words.', self.app.text.get('1.0', 'end'))
