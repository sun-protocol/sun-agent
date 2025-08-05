PROMPT_FOR_IMAGE_PROMPT = """
# Your Task
Generate a vivid, detailed image prompt based on the input content. The prompt should:
- Accurately capture the main idea of the content.
- Be directly usable by a text-to-image model.
- Output ONLY the English prompt, do NOT include your thought process.

# Image Style
{image_style}

# Three-Step Method

## Step 1:  Extract Core Theme
Extract 3-5 important keywords from the content, such as:
- Read the content carefully.
- Identify the most essential theme, object, action, or emotion.
- Ignore irrelevant details, hashtags, and secondary information.

## Step 2: Visualize It
- Translate the core theme into one or two simple, recognizable visual symbols.
- Use imagination: consider metaphorical, creative, or bold representations—not just literal objects.
- Choose the most vivid and original symbols that capture the content’s essence.

## Step 3: Compose the Prompt
Use the following structure:
- [Design Style], [Core Visual Elements], [Composition & Background], [Key Modifiers]
Guidelines:
- Use clear, concise English keywords.
- Focus on the central subject of the icon.
- Specify the background: "on a white background", "on a simple background", or "on a transparent background".
- Add key modifiers: app icon, vector logo, UI icon, clean, modern, vibrant colors.
"""