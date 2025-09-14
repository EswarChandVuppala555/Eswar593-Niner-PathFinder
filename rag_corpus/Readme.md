## Folders & Processes

**original/ug_cat/docx** contains the original undergraduate catalog in word format, unmodified

**processing/ug_cat/docx_exploded** contains the raw .docx files manually split into smaller files of no more than a few thousand words to enable format conversion by off-the-shelf LLMs like Claude 3.x Sonnet.  Note that this approach was not ultimately used.

**processing/ug_cat/md_claude_from_docx** contains markdown files converted from the exploded .docx files using Claude 3.7 Sonnet in concise mode on 2025-03-11 using the following prompt:

'Please convert this file to Markdown. Do not omit, add, or change any words. Convert any images that consist of text into text. Exclude all other images. Respond with a downloadable Markdown file, not in chat. Where a header spans multiple lines, combine them into a single-line header.'

**processing/ug_cat/md_pandoc_from_docx** contains markdown files converted from docx using the Pandoc utility.  This mostly worked for courses in the 2024-2025 catalog, although some manual cleanup was required.  It didn't work adequately for catalog chapters with more noise and hierarchical complexity. 

**processing/ug_cat/fc_html** contains html files scraped from UNC Charlotte's online catalog using Firecrawl.  These were ultimately dropped in favor of having Firecrawl attempt conversion to markdown.

**processing/ug_cat/fc_md** contains markdown files scraped from UNC Charlotte's online catalog using Firecrawl.

**staged/ug_cat/
