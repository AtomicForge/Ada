import unittest

from ada.render import parse


def plain(text):
    return "".join(piece for piece, _ in parse(text))


class RenderTests(unittest.TestCase):
    def test_plain_text_is_one_unchanged_piece(self):
        self.assertEqual(parse("Hello world."), [("Hello world.", None)])

    def test_empty_text(self):
        self.assertEqual(parse(""), [])

    def test_headings(self):
        self.assertEqual(parse("# One"), [("One", "h1")])
        self.assertEqual(parse("## Two"), [("Two", "h2")])
        self.assertEqual(parse("### Three"), [("Three", "h3")])
        self.assertEqual(parse("###### Six"), [("Six", "h3")])

    def test_hash_without_space_is_text(self):
        self.assertEqual(parse("#hashtag"), [("#hashtag", None)])

    def test_bold_italic_code(self):
        self.assertEqual(parse("a **b** c"), [("a ", None), ("b", "bold"), (" c", None)])
        self.assertEqual(parse("a *b* c"), [("a ", None), ("b", "italic"), (" c", None)])
        self.assertEqual(parse("a `b` c"), [("a ", None), ("b", "code"), (" c", None)])

    def test_unclosed_markers_stay_text(self):
        self.assertEqual(plain("**open and `open"), "**open and `open")

    def test_math_stars_stay_text(self):
        self.assertEqual(plain("2 * 3 * 4"), "2 * 3 * 4")
        self.assertEqual(plain("snake_case and a*b"), "snake_case and a*b")

    def test_bullets(self):
        self.assertEqual(parse("- one")[0], ("• ", None))
        self.assertEqual(plain("* one\n  - two"), "• one\n  • two")

    def test_bullet_with_bold(self):
        self.assertIn(("x", "bold"), parse("- **x** y"))

    def test_code_block_is_not_interpreted(self):
        pieces = parse("```python\n# not a heading **no**\n```\nafter")
        self.assertEqual(pieces[0], ("# not a heading **no**", "codeblock"))
        self.assertEqual(plain("```\nx\n```"), "x\n")
        self.assertEqual(pieces[-1], ("after", None))

    def test_unclosed_fence_keeps_rest_as_code(self):
        self.assertEqual(parse("```\na\nb"), [("a", "codeblock"), ("\n", None), ("b", "codeblock")])

    def test_links_images_and_html_stay_plain_text(self):
        for text in ("[click](http://evil.example)", "![x](http://evil.example/a.png)",
                     "<b>bold</b> <script>x</script>", "<img src=x onerror=y>"):
            self.assertEqual(parse(text), [(text, None)])

    def test_newlines_and_text_are_preserved(self):
        text = "Title\n\nline one\nline two\n\n"
        self.assertEqual(plain(text), text)

    def test_only_known_tags(self):
        text = "# h\n**b** *i* `c`\n- x\n```\ncode\n```\n"
        for _, tag in parse(text):
            self.assertIn(tag, (None, "bold", "italic", "code", "codeblock", "h1", "h2", "h3"))


if __name__ == "__main__":
    unittest.main()
