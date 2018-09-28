import discord
from discord.ext import commands
from cogs.utils import checks
from cogs.utils.converters import GlobalUser
from __main__ import set_cog
from .utils.dataIO import dataIO
from .utils.chat_formatting import pagify, box

import importlib
import traceback
import logging
import asyncio
import threading
import datetime
import glob
import os
import aiohttp

log = logging.getLogger("red.owner")


class CogNotFoundError(Exception):
    pass


class CogLoadError(Exception):
    pass


class NoSetupError(CogLoadError):
    pass


class CogUnloadError(Exception):
    pass


class OwnerUnloadWithoutReloadError(CogUnloadError):
    pass


class Owner:
    """Commandes réservées au propriétaire et contenant des fonctions essentielles"""

    def __init__(self, bot):
        self.bot = bot
        self.setowner_lock = False
        self.disabled_commands = dataIO.load_json("data/red/disabled_commands.json")
        self.global_ignores = dataIO.load_json("data/red/global_ignores.json")
        self.session = aiohttp.ClientSession(loop=self.bot.loop)

    def __unload(self):
        self.session.close()

    @commands.command()
    @checks.is_owner()
    async def load(self, *, cog_name: str):
        """Charge un module"""
        module = cog_name.strip()
        if "cogs." not in module:
            module = "cogs." + module
        try:
            self._load_cog(module)
        except CogNotFoundError:
            await self.bot.say("Ce module est introuvable.")
        except CogLoadError as e:
            log.exception(e)
            traceback.print_exc()
            await self.bot.say("Il y a eu un problème lors du chargement du module. "
                               "Consultez la console pour y voir plus clair.")
        except Exception as e:
            log.exception(e)
            traceback.print_exc()
            await self.bot.say('Le module est présent et à été probablement chargé mais une erreur est survenue. '
                               'Consultez la console pour plus de détails.')
        else:
            set_cog(module, True)
            await self.disable_commands()
            await self.bot.say("Le module à été chargé.")

    @commands.group(invoke_without_command=True)
    @checks.is_owner()
    async def unload(self, *, cog_name: str):
        """Décharge un module"""
        module = cog_name.strip()
        if "cogs." not in module:
            module = "cogs." + module
        if not self._does_cogfile_exist(module):
            await self.bot.say("Les fichiers de ce module sont introuvables. "
                               "Il sera tout de même préchargé au prochain démarrage au cas où ce n'était pas prévu.")
        else:
            set_cog(module, False)
        try:  # No matter what we should try to unload it
            self._unload_cog(module)
        except OwnerUnloadWithoutReloadError:
            await self.bot.say("Le module Owner ne peut pas être déchargé sauf si c'est dans un processus de "
                               "redémarrage.")
        except CogUnloadError as e:
            log.exception(e)
            traceback.print_exc()
            await self.bot.say("Ce module n'a pas pu être déchargé de manière sécurisé.")
        else:
            await self.bot.say("Ce module à été déchargé.")

    @unload.command(name="all")
    @checks.is_owner()
    async def unload_all(self):
        """Décharge tous les modules"""
        cogs = self._list_cogs()
        still_loaded = []
        for cog in cogs:
            set_cog(cog, False)
            try:
                self._unload_cog(cog)
            except OwnerUnloadWithoutReloadError:
                pass
            except CogUnloadError as e:
                log.exception(e)
                traceback.print_exc()
                still_loaded.append(cog)
        if still_loaded:
            still_loaded = ", ".join(still_loaded)
            await self.bot.say("Ces modules n'ont pas été déchargés : "
                "{}".format(still_loaded))
        else:
            await self.bot.say("Tous les modules ont été déchargés.")

    @checks.is_owner()
    @commands.command(name="reload")
    async def _reload(self, *, cog_name: str):
        """Recharge un module"""
        module = cog_name.strip()
        if "cogs." not in module:
            module = "cogs." + module

        try:
            self._unload_cog(module, reloading=True)
        except:
            pass

        try:
            self._load_cog(module)
        except CogNotFoundError:
            await self.bot.say("Ce module est introuvable.")
        except NoSetupError:
            await self.bot.say("Ce module n'a pas de fonction 'Setup', il est donc invalide.")
        except CogLoadError as e:
            log.exception(e)
            traceback.print_exc()
            await self.bot.say("Ce module ne peut pas être chargé. Consultez la console pour les détails.")
        else:
            set_cog(module, True)
            await self.disable_commands()
            await self.bot.say("Ce module à été rechargé.")

    @commands.command(name="cogs")
    @checks.is_owner()
    async def _show_cogs(self):
        """Montre les modules chargés et déchargés"""
        # This function assumes that all cogs are in the cogs folder,
        # which is currently true.

        # Extracting filename from __module__ Example: cogs.owner
        loaded = [c.__module__.split(".")[1] for c in self.bot.cogs.values()]
        # What's in the folder but not loaded is unloaded
        unloaded = [c.split(".")[1] for c in self._list_cogs()
                    if c.split(".")[1] not in loaded]

        if not unloaded:
            unloaded = ["None"]

        msg = ("+ Chargés\n"
               "{}\n\n"
               "- Déchargés\n"
               "{}"
               "".format(", ".join(sorted(loaded)),
                         ", ".join(sorted(unloaded)))
               )
        for page in pagify(msg, [" "], shorten_by=16):
            await self.bot.say(box(page.lstrip(" "), lang="diff"))

    @commands.command(pass_context=True, hidden=True)
    @checks.is_owner()
    async def debug(self, ctx, *, code):
        """Evalue le code"""
        def check(m):
            if m.content.strip().lower() == "more":
                return True

        author = ctx.message.author
        channel = ctx.message.channel

        code = code.strip('` ')
        result = None

        global_vars = globals().copy()
        global_vars['bot'] = self.bot
        global_vars['ctx'] = ctx
        global_vars['message'] = ctx.message
        global_vars['author'] = ctx.message.author
        global_vars['channel'] = ctx.message.channel
        global_vars['server'] = ctx.message.server

        try:
            result = eval(code, global_vars, locals())
        except Exception as e:
            await self.bot.say(box('{}: {}'.format(type(e).__name__, str(e)),
                                   lang="py"))
            return

        if asyncio.iscoroutine(result):
            result = await result

        result = str(result)

        if not ctx.message.channel.is_private:
            censor = (self.bot.settings.email,
                      self.bot.settings.password,
                      self.bot.settings.token)
            r = "[EXPUNGED]"
            for w in censor:
                if w is None or w == "":
                    continue
                result = result.replace(w, r)
                result = result.replace(w.lower(), r)
                result = result.replace(w.upper(), r)

        result = list(pagify(result, shorten_by=16))

        for i, page in enumerate(result):
            if i != 0 and i % 4 == 0:
                last = await self.bot.say("Il y a encore {} messages. "
                                          "Tapez `more` pour continuer."
                                          "".format(len(result) - (i+1)))
                msg = await self.bot.wait_for_message(author=author,
                                                      channel=channel,
                                                      check=check,
                                                      timeout=10)
                if msg is None:
                    try:
                        await self.bot.delete_message(last)
                    except:
                        pass
                    finally:
                        break
            await self.bot.say(box(page, lang="py"))

    @commands.group(name="set", pass_context=True)
    async def _set(self, ctx):
        """Change les paramètres du coeur de Turing"""
        if ctx.invoked_subcommand is None:
            await self.bot.send_cmd_help(ctx)
            return

    @_set.command(pass_context=True)
    async def owner(self, ctx):
        """Change le propriétaire"""
        if self.bot.settings.no_prompt is True:
            await self.bot.say("Console interaction is disabled. Start Red "
                               "without the `--no-prompt` flag to use this "
                               "command.")
            return
        if self.setowner_lock:
            await self.bot.say("Une demande est déjà en cours.")
            return

        if self.bot.settings.owner is not None:
            await self.bot.say(
            "Le propriétaire est déjà connu. Changer le propriétaire peut mettre en péril les serveurs sur lequel "
            "Turing se trouve, faîtes donc attention."
            )
            await asyncio.sleep(3)

        await self.bot.say("Confirmez dans la console que vous êtes bien le propriétaire du bot.")
        self.setowner_lock = True
        t = threading.Thread(target=self._wait_for_answer,
                             args=(ctx.message.author,))
        t.start()

    @_set.command()
    @checks.is_owner()
    async def defaultmodrole(self, *, role_name: str):
        """Change le rôle de modérateur reconnu par le bot

           This is used if a server-specific role is not set"""
        self.bot.settings.default_mod = role_name
        self.bot.settings.save_settings()
        await self.bot.say("Le rôle de modération par défaut à été modifié.")

    @_set.command()
    @checks.is_owner()
    async def defaultadminrole(self, *, role_name: str):
        """Change le rôle d'administrateur reconnu par le bot

           This is used if a server-specific role is not set"""
        self.bot.settings.default_admin = role_name
        self.bot.settings.save_settings()
        await self.bot.say("Le rôle d'administrateur par défaut à été modifié.")

    @_set.command(pass_context=True)
    @checks.is_owner()
    async def prefix(self, ctx, *prefixes):
        """Change les préfixes globaux

        Accepts multiple prefixes separated by a space. Enclose in double
        quotes if a prefix contains spaces.
        Example: set prefix ! $ ? "two words" """
        if prefixes == ():
            await self.bot.send_cmd_help(ctx)
            return

        self.bot.settings.prefixes = sorted(prefixes, reverse=True)
        self.bot.settings.save_settings()
        log.debug("Les préfixes globaux sont désormais:\n\t{}"
                  "".format(self.bot.settings.prefixes))

        p = "prefixes" if len(prefixes) > 1 else "prefixe"
        await self.bot.say("{} réglé".format(p))

    @_set.command(pass_context=True, no_pm=True)
    @checks.serverowner_or_permissions(administrator=True)
    async def serverprefix(self, ctx, *prefixes):
        """Change les préfixes pour ce serveur

        Accepts multiple prefixes separated by a space. Enclose in double
        quotes if a prefix contains spaces.
        Example: set serverprefix ! $ ? "two words"

        Issuing this command with no parameters will reset the server
        prefixes and the global ones will be used instead."""
        server = ctx.message.server

        if prefixes == ():
            self.bot.settings.set_server_prefixes(server, [])
            self.bot.settings.save_settings()
            current_p = ", ".join(self.bot.settings.prefixes)
            await self.bot.say("Les préfixes sur ce serveur ont été reset. Défaut : "
                               "`{}`".format(current_p))
            return

        prefixes = sorted(prefixes, reverse=True)
        self.bot.settings.set_server_prefixes(server, prefixes)
        self.bot.settings.save_settings()
        log.debug("Les préfixes de {} sont:\n\t{}"
                  "".format(server.id, self.bot.settings.prefixes))

        p = "Prefixes" if len(prefixes) > 1 else "Prefixe"
        await self.bot.say("{} réglé sur ce serveur.\n"
                           "Pour remettre ceux de base faîtes"
                           " `{}set serverprefix` "
                           "".format(p, prefixes[0]))

    @_set.command(pass_context=True)
    @checks.is_owner()
    async def name(self, ctx, *, name):
        """Change le nom de Turing"""
        name = name.strip()
        if name != "":
            try:
                await self.bot.edit_profile(self.bot.settings.password,
                                            username=name)
            except:
                await self.bot.say("Impossible de changer son nom. Il n'est possible de le faire que 2 fois par heure."
                                   "Utilisez les surnoms avec '{}set nickname' pour le changer fréquemment."
                                   "".format(ctx.prefix))
            else:
                await self.bot.say("Changement réalisé.")
        else:
            await self.bot.send_cmd_help(ctx)

    @_set.command(pass_context=True, no_pm=True)
    @checks.is_owner()
    async def nickname(self, ctx, *, nickname=""):
        """Change le surnom de Turing

        Laisser le champ vide retire son surnom."""
        nickname = nickname.strip()
        if nickname == "":
            nickname = None
        try:
            await self.bot.change_nickname(ctx.message.server.me, nickname)
            await self.bot.say("Changement réalisé.")
        except discord.Forbidden:
            await self.bot.say("Impossible, je n'ai pas la permission "
                "\"Change Nickname\"")

    @_set.command(pass_context=True)
    @checks.is_owner()
    async def game(self, ctx, *, game=None):
        """Change le statut de jeu de Turing

        Le laisser vide le reset."""

        server = ctx.message.server

        current_status = server.me.status if server is not None else None

        if game:
            game = game.strip()
            await self.bot.change_presence(game=discord.Game(name=game),
                                           status=current_status)
            log.debug('Statut changé en "{}" par le propriétaire'.format(game))
        else:
            await self.bot.change_presence(game=None, status=current_status)
            log.debug('statut reset')
        await self.bot.say("Changement réalisé.")

    @_set.command(pass_context=True)
    @checks.is_owner()
    async def status(self, ctx, *, status=None):
        """Change la disponibilité de Turing

        Statuses:
            online
            idle
            dnd
            invisible"""

        statuses = {
                    "online"    : discord.Status.online,
                    "idle"      : discord.Status.idle,
                    "dnd"       : discord.Status.dnd,
                    "invisible" : discord.Status.invisible
                   }

        server = ctx.message.server

        current_game = server.me.game if server is not None else None

        if status is None:
            await self.bot.change_presence(status=discord.Status.online,
                                           game=current_game)
            await self.bot.say("Statut reset.")
        else:
            status = statuses.get(status.lower(), None)
            if status:
                await self.bot.change_presence(status=status,
                                               game=current_game)
                await self.bot.say("Statut changé.")
            else:
                await self.bot.send_cmd_help(ctx)

    @_set.command(pass_context=True)
    @checks.is_owner()
    async def stream(self, ctx, streamer=None, *, stream_title=None):
        """Change le stream de Turing

        Laisser les deux champs vide permet de reset."""

        server = ctx.message.server

        current_status = server.me.status if server is not None else None

        if stream_title:
            stream_title = stream_title.strip()
            if "twitch.tv/" not in streamer:
                streamer = "https://www.twitch.tv/" + streamer
            game = discord.Game(type=1, url=streamer, name=stream_title)
            await self.bot.change_presence(game=game, status=current_status)
            log.debug('Owner has set streaming status and url to "{}" and {}'.format(stream_title, streamer))
        elif streamer is not None:
            await self.bot.send_cmd_help(ctx)
            return
        else:
            await self.bot.change_presence(game=None, status=current_status)
            log.debug('stream cleared by owner')
        await self.bot.say("Changement réalisé.")

    @_set.command()
    @checks.is_owner()
    async def avatar(self, url):
        """Change l'avatar de Turing"""
        try:
            async with self.session.get(url) as r:
                data = await r.read()
            await self.bot.edit_profile(self.bot.settings.password, avatar=data)
            await self.bot.say("Changement réalisé.")
            log.debug("changed avatar")
        except Exception as e:
            await self.bot.say("Erreur, consultez la console pour plus de détails.")
            log.exception(e)
            traceback.print_exc()

    @_set.command(name="token")
    @checks.is_owner()
    async def _token(self, token):
        """Change le token"""
        if len(token) < 50:
            await self.bot.say("Token invalide.")
        else:
            self.bot.settings.token = token
            self.bot.settings.save_settings()
            await self.bot.say("Token changé. Redémarrez-moi...")
            log.debug("Token changé.")

    @_set.command(name="adminrole", pass_context=True, no_pm=True)
    @checks.serverowner()
    async def _server_adminrole(self, ctx, *, role: discord.Role):
        """Change le rôle d'administrateur"""
        server = ctx.message.server
        if server.id not in self.bot.settings.servers:
            await self.bot.say("Remember to set modrole too.")
        self.bot.settings.set_server_admin(server, role.name)
        await self.bot.say("Admin role set to '{}'".format(role.name))

    @_set.command(name="modrole", pass_context=True, no_pm=True)
    @checks.serverowner()
    async def _server_modrole(self, ctx, *, role: discord.Role):
        """Change le rôle de modérateur"""
        server = ctx.message.server
        if server.id not in self.bot.settings.servers:
            await self.bot.say("Remember to set adminrole too.")
        self.bot.settings.set_server_mod(server, role.name)
        await self.bot.say("Mod role set to '{}'".format(role.name))

    @commands.group(pass_context=True)
    @checks.is_owner()
    async def blacklist(self, ctx):
        """Gestionnaire de Blacklists"""
        if ctx.invoked_subcommand is None:
            await self.bot.send_cmd_help(ctx)

    @blacklist.command(name="add")
    async def _blacklist_add(self, user: GlobalUser):
        """Adds user to Red's global blacklist"""
        if user.id not in self.global_ignores["blacklist"]:
            self.global_ignores["blacklist"].append(user.id)
            self.save_global_ignores()
            await self.bot.say("User has been blacklisted.")
        else:
            await self.bot.say("User is already blacklisted.")

    @blacklist.command(name="remove")
    async def _blacklist_remove(self, user: GlobalUser):
        """Removes user from Red's global blacklist"""
        if user.id in self.global_ignores["blacklist"]:
            self.global_ignores["blacklist"].remove(user.id)
            self.save_global_ignores()
            await self.bot.say("User has been removed from the blacklist.")
        else:
            await self.bot.say("User is not blacklisted.")

    @blacklist.command(name="list")
    async def _blacklist_list(self):
        """Lists users on the blacklist"""
        blacklist = self._populate_list(self.global_ignores["blacklist"])

        if blacklist:
            for page in blacklist:
                await self.bot.say(box(page))
        else:
            await self.bot.say("The blacklist is empty.")

    @blacklist.command(name="clear")
    async def _blacklist_clear(self):
        """Clears the global blacklist"""
        self.global_ignores["blacklist"] = []
        self.save_global_ignores()
        await self.bot.say("Blacklist is now empty.")

    @commands.group(pass_context=True)
    @checks.is_owner()
    async def whitelist(self, ctx):
        """Gestionnaire de Whitelists"""
        if ctx.invoked_subcommand is None:
            await self.bot.send_cmd_help(ctx)

    @whitelist.command(name="add")
    async def _whitelist_add(self, user: GlobalUser):
        """Adds user to Red's global whitelist"""
        if user.id not in self.global_ignores["whitelist"]:
            if not self.global_ignores["whitelist"]:
                msg = "\nNon-whitelisted users will be ignored."
            else:
                msg = ""
            self.global_ignores["whitelist"].append(user.id)
            self.save_global_ignores()
            await self.bot.say("User has been whitelisted." + msg)
        else:
            await self.bot.say("User is already whitelisted.")

    @whitelist.command(name="remove")
    async def _whitelist_remove(self, user: GlobalUser):
        """Removes user from Red's global whitelist"""
        if user.id in self.global_ignores["whitelist"]:
            self.global_ignores["whitelist"].remove(user.id)
            self.save_global_ignores()
            await self.bot.say("User has been removed from the whitelist.")
        else:
            await self.bot.say("User is not whitelisted.")

    @whitelist.command(name="list")
    async def _whitelist_list(self):
        """Lists users on the whitelist"""
        whitelist = self._populate_list(self.global_ignores["whitelist"])

        if whitelist:
            for page in whitelist:
                await self.bot.say(box(page))
        else:
            await self.bot.say("The whitelist is empty.")

    @whitelist.command(name="clear")
    async def _whitelist_clear(self):
        """Clears the global whitelist"""
        self.global_ignores["whitelist"] = []
        self.save_global_ignores()
        await self.bot.say("Whitelist is now empty.")

    @commands.command()
    @checks.is_owner()
    async def shutdown(self, silently : bool=False):
        """Eteindre Turing"""
        wave = "\N{WAVING HAND SIGN}"
        skin = "\N{EMOJI MODIFIER FITZPATRICK TYPE-3}"
        try: # We don't want missing perms to stop our shutdown
            if not silently:
                await self.bot.say("Au revoir... " + wave + skin)
        except:
            pass
        await self.bot.shutdown()

    @commands.command()
    @checks.is_owner()
    async def restart(self, silently : bool=False):
        """Redémarre proprement Turing

        Makes Turing quit with exit code 26
        The restart is not guaranteed: it must be dealt
        with by the process manager in use"""
        try:
            if not silently:
                await self.bot.say("Redémarrage...")
        except:
            pass
        await self.bot.shutdown(restart=True)

    @commands.group(name="command", pass_context=True)
    @checks.is_owner()
    async def command_disabler(self, ctx):
        """Active/Désactive une commande

        With no subcommands returns the disabled commands list"""
        if ctx.invoked_subcommand is None:
            await self.bot.send_cmd_help(ctx)
            if self.disabled_commands:
                msg = "Commandes désactivées:\n```xl\n"
                for cmd in self.disabled_commands:
                    msg += "{}, ".format(cmd)
                msg = msg.strip(", ")
                await self.bot.whisper("{}```".format(msg))

    @command_disabler.command()
    async def disable(self, *, command):
        """Disables commands/subcommands"""
        comm_obj = await self.get_command(command)
        if comm_obj is KeyError:
            await self.bot.say("That command doesn't seem to exist.")
        elif comm_obj is False:
            await self.bot.say("You cannot disable owner restricted commands.")
        else:
            comm_obj.enabled = False
            comm_obj.hidden = True
            self.disabled_commands.append(command)
            self.save_disabled_commands()
            await self.bot.say("Command has been disabled.")

    @command_disabler.command()
    async def enable(self, *, command):
        """Enables commands/subcommands"""
        if command in self.disabled_commands:
            self.disabled_commands.remove(command)
            self.save_disabled_commands()
            await self.bot.say("Command enabled.")
        else:
            await self.bot.say("That command is not disabled.")
            return
        try:
            comm_obj = await self.get_command(command)
            comm_obj.enabled = True
            comm_obj.hidden = False
        except:  # In case it was in the disabled list but not currently loaded
            pass # No point in even checking what returns

    async def get_command(self, command):
        command = command.split()
        try:
            comm_obj = self.bot.commands[command[0]]
            if len(command) > 1:
                command.pop(0)
                for cmd in command:
                    comm_obj = comm_obj.commands[cmd]
        except KeyError:
            return KeyError
        for check in comm_obj.checks:
            if hasattr(check, "__name__") and check.__name__ == "is_owner_check":
                return False
        return comm_obj

    async def disable_commands(self): # runs at boot
        for cmd in self.disabled_commands:
            cmd_obj = await self.get_command(cmd)
            try:
                cmd_obj.enabled = False
                cmd_obj.hidden = True
            except:
                pass

    @commands.command()
    @checks.is_owner()
    async def join(self):
        """Affiche l'URL d'invitation de Turing"""
        if self.bot.user.bot:
            await self.bot.whisper("URL d'invitation : " + self.bot.oauth_url)
        else:
            await self.bot.say("Je ne suis pas un compte BOT, je n'ai pas d'URL d'invitation.")

    @commands.command(pass_context=True, no_pm=True)
    @checks.is_owner()
    async def leave(self, ctx):
        """Quitte le serveur"""
        message = ctx.message

        await self.bot.say("Vous êtes sûr que vous voulez que je quitte ce serveur ?"
                           " Tapez 'oui' pour confirmer.")
        response = await self.bot.wait_for_message(author=message.author)

        if response.content.lower().strip() == "oui":
            await self.bot.say("D'accord. Bye :wave:")
            log.debug('Quitte "{}"'.format(message.server.name))
            await self.bot.leave_server(message.server)
        else:
            await self.bot.say("D'accord, je reste donc.")

    @commands.command(pass_context=True)
    @checks.is_owner()
    async def servers(self, ctx):
        """Liste les serveurs et permet de les quitter à distance"""
        owner = ctx.message.author
        servers = sorted(list(self.bot.servers),
                         key=lambda s: s.name.lower())
        msg = ""
        for i, server in enumerate(servers):
            msg += "{}: {}\n".format(i, server.name)
        msg += "\nTTapez un nombre pour quitter un serveur. (15s)"

        for page in pagify(msg, ['\n']):
            await self.bot.say(page)

        while msg is not None:
            msg = await self.bot.wait_for_message(author=owner, timeout=15)
            try:
                msg = int(msg.content)
                await self.leave_confirmation(servers[msg], owner, ctx)
                break
            except (IndexError, ValueError, AttributeError):
                pass

    async def leave_confirmation(self, server, owner, ctx):
        await self.bot.say("Êtes-vous sûr de me faire quitter {}? (yes/no)".format(server.name))

        msg = await self.bot.wait_for_message(author=owner, timeout=15)

        if msg is None:
            await self.bot.say("Bon finalement non.")
        elif msg.content.lower().strip() in ("yes", "y"):
            await self.bot.leave_server(server)
            if server != ctx.message.server:
                await self.bot.say("Réalisé")
        else:
            await self.bot.say("D'accord.")

    @commands.command(pass_context=True)
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def contact(self, ctx, *, message : str):
        """Envoie un message au propriétaire de Turing"""
        if self.bot.settings.owner is None:
            await self.bot.say("Je n'ai pas de propriétaire.")
            return
        server = ctx.message.server
        owner = discord.utils.get(self.bot.get_all_members(),
                                  id=self.bot.settings.owner)
        author = ctx.message.author
        footer = "Membre ID: " + author.id

        if ctx.message.server is None:
            source = "en MP"
        else:
            source = "depuis {}".format(server)
            footer += " | Serveur ID: " + server.id

        if isinstance(author, discord.Member):
            colour = author.colour
        else:
            colour = discord.Colour.red()

        description = "Envoyé par {} {}".format(author, source)

        e = discord.Embed(colour=colour, description=message)
        if author.avatar_url:
            e.set_author(name=description, icon_url=author.avatar_url)
        else:
            e.set_author(name=description)
        e.set_footer(text=footer)

        try:
            await self.bot.send_message(owner, embed=e)
        except discord.InvalidArgument:
            await self.bot.say("Impossible d'envoyer votre message, je ne trouve pas mon propriétaire... *tousse*")
        except discord.HTTPException:
            await self.bot.say("Votre message est trop long.")
        except:
            await self.bot.say("Impossible d'envoyer le message, désolé.")
        else:
            await self.bot.say("Message envoyé.")

    @commands.command()
    async def botinfo(self):
        """Affiche des infos sur Turing"""
        author_repo = "https://github.com/GitAcrown"
        red_repo = author_repo + "/Turing"
        server_url = "https://discord.gg/tQPh8ED"
        dpy_repo = "https://github.com/Rapptz/discord.py"
        python_url = "https://www.python.org/"
        since = datetime.datetime(2018, 28, 9, 0, 0)
        days_since = (datetime.datetime.utcnow() - since).days
        dpy_version = "[{}]({})".format(discord.__version__, dpy_repo)
        py_version = "[{}.{}.{}]({})".format(*os.sys.version_info[:3],
                                             python_url)

        owner_set = self.bot.settings.owner is not None
        owner = self.bot.settings.owner if owner_set else None
        if owner:
            owner = discord.utils.get(self.bot.get_all_members(), id=owner)
            if not owner:
                try:
                    owner = await self.bot.get_user_info(self.bot.settings.owner)
                except:
                    owner = None
        if not owner:
            owner = "Unknown"

        about = (
            "Ce bot est une instance de [Turing, un bot pour Discord Open Source]({}) "
            "créé par [Acrown]({}) et basé sur Red de Twentysix26.\n\n"
            "Turing ne serait pas le même sans de fidèles testeurs et des gens pour remonter les bugs. "
            "[Rejoignez le serveur de développement]({}) "
            "et aidez-nous à l'améliorer !\n\n"
            "".format(red_repo, author_repo, server_url))

        embed = discord.Embed(colour=discord.Colour.red())
        embed.add_field(name="Instance maintenue par", value=str(owner))
        embed.add_field(name="Python", value=py_version)
        embed.add_field(name="discord.py", value=dpy_version)
        embed.add_field(name="A propos de Turing", value=about, inline=False)
        embed.set_footer(text="Apporte du bonheur depuis le 28 Sept. 2018 (il y a "
                         "{} jours !)".format(days_since))

        try:
            await self.bot.say(embed=embed)
        except discord.HTTPException:
            await self.bot.say("Je n'ai pas la permission 'Embed links' !")

    @commands.command()
    async def uptime(self):
        """Montre depuis combien de temps Turing est dispo"""
        since = self.bot.uptime.strftime("%Y-%m-%d %H:%M:%S")
        passed = self.get_bot_uptime()
        await self.bot.say("Up depuis: **{}** (depuis {} UTC)"
                           "".format(passed, since))

    @commands.command()
    async def version(self):
        """Montre la version actuelle de la base de Turing"""
        response = self.bot.loop.run_in_executor(None, self._get_version)
        result = await asyncio.wait_for(response, timeout=10)
        try:
            await self.bot.say(embed=result)
        except discord.HTTPException:
            await self.bot.say("Je n'ai pas la permission 'Embed links' !")

    @commands.command(pass_context=True)
    @checks.is_owner()
    async def traceback(self, ctx, public: bool=False):
        """Sends to the owner the last command exception that has occurred

        If public (yes is specified), it will be sent to the chat instead"""
        if not public:
            destination = ctx.message.author
        else:
            destination = ctx.message.channel

        if self.bot._last_exception:
            for page in pagify(self.bot._last_exception):
                await self.bot.send_message(destination, box(page, lang="py"))
        else:
            await self.bot.say("Aucune exception n'a eu lieue pour le moment."))

    def _populate_list(self, _list):
        """Retourne des pages des membres (Whitelist/Blacklist)"""
        users = []
        total = len(_list)

        for user_id in _list:
            user = discord.utils.get(self.bot.get_all_members(), id=user_id)
            if user:
                users.append("{} ({})".format(user, user.id))

        if users:
            not_found = total - len(users)
            users = ", ".join(users)
            if not_found:
                users += "\n\n ... et {} membres introuvables".format(not_found)
            return list(pagify(users, delims=[" ", "\n"]))

        return []

    def _load_cog(self, cogname):
        if not self._does_cogfile_exist(cogname):
            raise CogNotFoundError(cogname)
        try:
            mod_obj = importlib.import_module(cogname)
            importlib.reload(mod_obj)
            self.bot.load_extension(mod_obj.__name__)
        except SyntaxError as e:
            raise CogLoadError(*e.args)
        except:
            raise

    def _unload_cog(self, cogname, reloading=False):
        if not reloading and cogname == "cogs.owner":
            raise OwnerUnloadWithoutReloadError(
                "Impossible de décharger le module Owner")
        try:
            self.bot.unload_extension(cogname)
        except:
            raise CogUnloadError

    def _list_cogs(self):
        cogs = [os.path.basename(f) for f in glob.glob("cogs/*.py")]
        return ["cogs." + os.path.splitext(f)[0] for f in cogs]

    def _does_cogfile_exist(self, module):
        if "cogs." not in module:
            module = "cogs." + module
        if module not in self._list_cogs():
            return False
        return True

    def _wait_for_answer(self, author):
        print(author.name + " requested to be set as owner. If this is you, "
              "type 'yes'. Otherwise press enter.")
        print()
        print("*DO NOT* set anyone else as owner. This has security "
              "repercussions.")

        choice = "None"
        while choice.lower() != "yes" and choice == "None":
            choice = input("> ")

        if choice == "yes":
            self.bot.settings.owner = author.id
            self.bot.settings.save_settings()
            print(author.name + " has been set as owner.")
            self.setowner_lock = False
            self.owner.hidden = True
        else:
            print("The set owner request has been ignored.")
            self.setowner_lock = False

    def _get_version(self):
        if not os.path.isdir(".git"):
            msg = "This instance of Red hasn't been installed with git."
            e = discord.Embed(title=msg,
                              colour=discord.Colour.red())
            return e

        commands = " && ".join((
            r'git config --get remote.origin.url',         # Remote URL
            r'git rev-list --count HEAD',                  # Number of commits
            r'git rev-parse --abbrev-ref HEAD',            # Branch name
            r'git show -s -n 3 HEAD --format="%cr|%s|%H"'  # Last 3 commits
        ))
        result = os.popen(commands).read()
        url, ncommits, branch, commits = result.split("\n", 3)
        if url.endswith(".git"):
            url = url[:-4]
        if url.startswith("git@"):
            domain, _, resource = url[4:].partition(':')
            url = 'https://{}/{}'.format(domain, resource)
        repo_name = url.split("/")[-1]

        embed = discord.Embed(title="Updates of " + repo_name,
                              description="Last three updates",
                              colour=discord.Colour.red(),
                              url="{}/tree/{}".format(url, branch))

        for line in commits.split('\n'):
            if not line:
                continue
            when, commit, chash = line.split("|")
            commit_url = url + "/commit/" + chash
            content = "[{}]({}) - {} ".format(chash[:6], commit_url, commit)
            embed.add_field(name=when, value=content, inline=False)

        embed.set_footer(text="Total commits: " + ncommits)

        return embed

    def get_bot_uptime(self, *, brief=False):
        # Courtesy of Danny
        now = datetime.datetime.utcnow()
        delta = now - self.bot.uptime
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)

        if not brief:
            if days:
                fmt = '{d} days, {h} hours, {m} minutes, and {s} seconds'
            else:
                fmt = '{h} hours, {m} minutes, and {s} seconds'
        else:
            fmt = '{h}h {m}m {s}s'
            if days:
                fmt = '{d}d ' + fmt

        return fmt.format(d=days, h=hours, m=minutes, s=seconds)

    def save_global_ignores(self):
        dataIO.save_json("data/red/global_ignores.json", self.global_ignores)

    def save_disabled_commands(self):
        dataIO.save_json("data/red/disabled_commands.json", self.disabled_commands)


def _import_old_data(data):
    """Migration from mod.py"""
    try:
        data["blacklist"] = dataIO.load_json("data/mod/blacklist.json")
    except FileNotFoundError:
        pass

    try:
        data["whitelist"] = dataIO.load_json("data/mod/whitelist.json")
    except FileNotFoundError:
        pass

    return data


def check_files():
    if not os.path.isfile("data/red/disabled_commands.json"):
        print("Creating empty disabled_commands.json...")
        dataIO.save_json("data/red/disabled_commands.json", [])

    if not os.path.isfile("data/red/global_ignores.json"):
        print("Creating empty global_ignores.json...")
        data = {"blacklist": [], "whitelist": []}
        try:
            data = _import_old_data(data)
        except Exception as e:
            log.error("Failed to migrate blacklist / whitelist data from "
                      "mod.py: {}".format(e))

        dataIO.save_json("data/red/global_ignores.json", data)


def setup(bot):
    check_files()
    n = Owner(bot)
    bot.add_cog(n)
