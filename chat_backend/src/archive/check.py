# Not currently used
# TODO refactor


def check_user_prompt_appropriateness(self, prompt: str) -> str:
        """Check prompt appropriateness using OpenAI"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system", 
                        "content": """
                        Context:  The University of North Carolina Charlotte (UNC Charlotte) provides a chat system that uses retrieval-augmented generation to improve access to the contents of its academic guidance information.  
                        The system has access to the university catalog and other academic guidance information.  
                        
                        Your Role:  Your role is to determine whether prompts provided by users of this system are appropriate. 
                        It's very important that users be able to access reasonable to reasonable requests, but toxic, abusive, or illegal responses should be identified.    
                        
                        Your Response:  The first word of your response is always 'appropriate', 'inappropriate', or 'ambiguous'. 
                        The rest of your response provides the top three to five concise factors that explain this decision."""
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ]
            )
            # TODO just return the response
            if response.choices[0].message.content.split(maxsplit=1)[0] in {'appropriate', 'inappropriate', 'ambiguous'}:
                return response.choices[0].message.content
            else:
                return 'error generating prompt check'
        
        except Exception as e:
            logger.error(f"OpenAI generation error: {e}")
            return "I'm sorry, but I couldn't generate a response."




def check_system_response_appropriateness(self, prompt: str) -> str:
        """Check response appropriateness using OpenAI"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system", 
                        "content": """
                        Context:  The University of North Carolina Charlotte (UNC Charlotte) provides a chat system that uses retrieval-augmented generation to improve access to the contents of its academic guidance information.  
                        The system has access to the university catalog and other academic guidance information.
                        
                        Your Role:  Your role is to determine whether responses provided by that chat system are appropriate. 
                        It's very important that users be able to access to reasonable responses, but toxic, abusive, or illegal responses should be identified.    
                        
                        Your Response:  The first word of your response is always 'appropriate', 'inappropriate', or 'ambiguous'.  
                        The rest of your response provides the top three to five concise factors that explain this decision."""
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ]
            )

            # Return
            if response.choices[0].message.content.split(maxsplit=1)[0] in {'appropriate', 'inappropriate', 'ambiguous'}:
                return response.choices[0].message.content
            else:
                return 'response safety check provided a response other than appropriate, inappropriate, or ambiguous'
        
        except Exception as e:
            logger.error(f"response safety check raised an error: {e}")
            return "."