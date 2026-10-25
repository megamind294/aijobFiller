"""Regressions for decorated headings and scanned two-column resumes."""
import unittest

from jobpilot.resume_uploads import suggested_profile


class ResumeLayoutTests(unittest.TestCase):
    def test_dates_between_employer_and_role_are_associated_with_that_record(self):
        draft = suggested_profile('''Jane Example
Experience
Example Studio
2022 - Present
Software Engineer
* Built customer forms.
Earlier Company
2019 - 2021
Associate App Developer
* Maintained dashboards.
Education
Example University
2015 - 2019
Bachelor of Technology in Computer Science
''')
        self.assertEqual(len(draft.get('experience', [])), 2)
        self.assertEqual(draft['experience'][1]['company'], 'Earlier Company')
        self.assertEqual(draft['education'][0]['institution'], 'Example University')

    def test_skills_preserve_commas_inside_parentheses(self):
        draft = suggested_profile('Jane Example\nSkills\n* Azure (Web App, DevOps, Maps), React\n')
        self.assertEqual(draft['skills'], ['Azure (Web App, DevOps, Maps)', 'React'])

    def test_plus_bullets_do_not_remove_plus_signs_from_skill_names(self):
        draft = suggested_profile('Jane Example\nSkills\n+ C++\nC, C++, .NET, Notepad++\n')
        self.assertEqual(draft['skills'], ['C++', 'C', '.NET', 'Notepad++'])

    def test_adjacent_records_keep_roles_with_their_original_employers(self):
        draft = suggested_profile('''Jane Example
Experience
Software Engineer
First Company
2020 - 2022
Senior Developer
Second Company
2022 - Present
''')
        self.assertEqual([(e['role'], e['company']) for e in draft['experience']],
                         [('Software Engineer', 'First Company'), ('Senior Developer', 'Second Company')])

    def test_decorated_headings_recover_sections_without_importing_other_content(self):
        draft = suggested_profile('''Jane Example
jane@example.com
& PROFESSIONAL SUMMARY
Frontend developer building accessible forms.
SS sCPROFESSIONAL EXPERIENCE
Example Studio | Jan 2022 - Present
Senior Software Engineer
* Built accessible forms.
</> PROJECTS
Accessible UI Kit
* Published reusable components.
TS EDUCATION
Example University | 2015 - 2019
Bachelor of Technology in Computer Science
* Graduated with honours.
{TECHNICAL SKILLS
* React, TypeScript
Cloud TOOLS & PLATFORMS
* Docker
LANGUAGES
English
1} CERTIFICATIONS
Example Certificate
''')
        self.assertEqual(draft['summary'], 'Frontend developer building accessible forms.')
        self.assertEqual(draft['experience'][0]['company'], 'Example Studio')
        self.assertEqual(draft['experience'][0]['role'], 'Senior Software Engineer')
        self.assertEqual(draft['experience'][0]['start'], 'Jan 2022')
        self.assertEqual(draft['experience'][0]['bullets'], ['Built accessible forms.'])
        self.assertEqual(draft['education'][0]['institution'], 'Example University')
        self.assertEqual(draft['education'][0]['degree'], 'Bachelor of Technology in Computer Science')
        self.assertEqual(draft['projects'][0]['name'], 'Accessible UI Kit')
        self.assertEqual(draft['skills'], ['React', 'TypeScript', 'Docker'])

    def test_heading_words_inside_a_sentence_do_not_start_sections(self):
        draft = suggested_profile('''Jane Example
Professional Summary
I build tools for teams with technical skills.
I enjoy sharing professional experience.
Skills
React
''')
        self.assertEqual(draft['summary'], 'I build tools for teams with technical skills. I enjoy sharing professional experience.')
        self.assertEqual(draft['skills'], ['React'])

    def test_ambiguous_dated_employer_does_not_invent_a_role(self):
        draft = suggested_profile('''Jane Example
Experience
Example Studio | 2022 - Present
Worked on several projects.
''')
        self.assertNotIn('experience', draft)

    def test_two_columns_are_read_separately_with_dates_next_to_employer(self):
        from jobpilot.resume_uploads import ordered_ocr_text
        lines = [('Jane Example', 100, 50, 300), ('Professional Summary', 50, 250, 400),
                 ('Frontend developer.', 50, 290, 400), ('Experience', 50, 340, 400),
                 ('Example Studio 2022 - Present', 50, 380, 500),
                 ('Software Engineer', 50, 420, 400), ('* Built customer forms.', 50, 460, 400),
                 ('Projects', 50, 500, 400), ('Accessible UI Kit', 50, 540, 400),
                 ('* Published components.', 50, 580, 400),
                 ('Technical Skills', 720, 250, 230), ('React', 720, 290, 230),
                 ('TypeScript', 720, 340, 230), ('CSS', 720, 380, 230),
                 ('Docker', 720, 420, 230), ('SQL', 720, 460, 230),
                 ('Git', 720, 500, 230), ('HTML', 720, 540, 230)]
        # Tesseract can emit headings from both columns before the left body.
        rows = ['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext',
                '1\t1\t0\t0\t0\t0\t0\t0\t1000\t1400\t-1\t']
        for i, (line, x, y, width) in enumerate(lines):
            rows.append(f'5\t1\t{i+1}\t1\t1\t1\t{x}\t{y}\t{width}\t20\t90\t{line}')
        fallback = 'interleaved OCR text'
        text = ordered_ocr_text('\n'.join(rows), fallback)
        draft = suggested_profile(text)
        self.assertEqual(draft['summary'], 'Frontend developer.')
        self.assertEqual(draft['experience'][0]['role'], 'Software Engineer')
        self.assertEqual(draft['projects'][0]['name'], 'Accessible UI Kit')
        self.assertIn('React', draft['skills'])
        self.assertNotIn('Frontend developer.', draft['skills'])

    def test_single_column_ocr_preserves_original_reading_order(self):
        from jobpilot.resume_uploads import ordered_ocr_text
        self.assertEqual(ordered_ocr_text('', 'Original text'), 'Original text')


if __name__ == '__main__':
    unittest.main()
