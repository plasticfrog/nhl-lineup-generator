def extract_players_from_image(image_file, expected_count, team_roster):
    """Extract names from OCR and match to actual roster"""
    try:
        image = Image.open(image_file)
        text = pytesseract.image_to_string(image, config='--psm 6')
        
        lines = text.split('\n')
        ocr_names = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            alpha = sum(1 for c in line if c.isalpha())
            upper = sum(1 for c in line if c.isupper())
            spaces = line.count(' ')
            
            # Good line criteria
            if alpha > 20 and upper > 15 and spaces >= 3:
                words = []
                for word in line.split():
                    clean = ''.join(c for c in word if c.isalpha())
                    if len(clean) >= 2:
                        words.append(clean.upper())
                
                # Split into name pairs
                for i in range(0, len(words)-1, 2):
                    if i+1 < len(words):
                        ocr_names.append(f"{words[i]} {words[i+1]}")
        
        # Now match OCR names to roster
        matched_names = []
        used_roster = set()
        
        for ocr_name in ocr_names:
            best_match = None
            best_score = 0
            
            for roster_name in team_roster:
                if roster_name in used_roster:
                    continue
                
                # Calculate similarity score
                score = 0
                ocr_parts = ocr_name.split()
                roster_parts = roster_name.split()
                
                # Check last name match
                if ocr_parts[-1] in roster_parts[-1] or roster_parts[-1] in ocr_parts[-1]:
                    score += 50
                
                # Check first name
                if len(ocr_parts) > 0 and len(roster_parts) > 0:
                    if ocr_parts[0][:3] == roster_parts[0][:3]:
                        score += 30
                
                # Check full substring match
                if ocr_name in roster_name or roster_name in ocr_name:
                    score += 20
                
                if score > best_score:
                    best_score = score
                    best_match = roster_name
            
            if best_match and best_score > 30:
                matched_names.append(best_match)
                used_roster.add(best_match)
            else:
                matched_names.append(ocr_name)  # Keep OCR if no good match
        
        # Pad with placeholders
        while len(matched_names) < expected_count:
            matched_names.append(f"PLAYER {len(matched_names)+1}")
        
        return matched_names[:expected_count]
        
    except Exception as e:
        print(f"OCR Error: {str(e)}")
        return [f"PLAYER {i+1}" for i in range(expected_count)]
