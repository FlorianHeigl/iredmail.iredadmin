# Author: Zhang Huangbin <zhb@iredmail.org>

import types
import web
import ldap
from ldap.filter import escape_filter_chars
from libs import iredutils
from libs.ldaplib import core, ldaputils, decorators, attrs, deltree

cfg = web.iredconfig
session = web.config.get('_session')

try:
    import cStringIO as StringIO
except ImportError:
    import StringIO


class Utils(core.LDAPWrap):
    def __del__(self):
        try:
            self.conn.unbind()
        except Exception, e:
            pass

    def addOrDelAttrValue(self, dn, attr, value, action):
        """Used to add or delete value of attribute which can handle multiple values.

        @attr: ldap attribute name
        @value: value of attr
        @action: add, delete.
        """
        self.dn = escape_filter_chars(dn)
        if action == 'add' or action == 'assign':
            try:
                self.conn.modify_s(self.dn, [(ldap.MOD_ADD, attr, value)])
                return (True,)
            except ldap.NO_SUCH_OBJECT:
                return (True,)
                #return (False, 'OBJECT_NOT_EXIST')
            except ldap.TYPE_OR_VALUE_EXISTS:
                return (True,)
            except Exception, e:
                return (False, ldaputils.getExceptionDesc(e))
        elif action == 'delete' or action == 'remove':
            try:
                self.conn.modify_s(self.dn, [(ldap.MOD_DELETE, attr, value)])
                return (True,)
            except ldap.NO_SUCH_ATTRIBUTE:
                return (True,)
            except Exception, e:
                return (False, ldaputils.getExceptionDesc(e))
        else:
            return (False, 'UNKNOWN_ACTION')

    # Change password.
    def changePasswd(self, dn, cur_passwd, newpw):
        dn = escape_filter_chars(dn)
        try:
            # Reference: RFC3062 - LDAP Password Modify Extended Operation
            self.conn.passwd_s(dn, cur_passwd, newpw)
            return (True,)
        except ldap.UNWILLING_TO_PERFORM:
            return (False, 'INCORRECT_OLDPW')
        except Exception, e:
            return (False, ldaputils.getExceptionDesc(e))

    # Update value of attribute which must be single value.
    def updateAttrSingleValue(self, dn, attr, value):
        self.mod_attrs = [
                (ldap.MOD_REPLACE, web.safestr(attr), web.safestr(value))
                ]
        try:
            result = self.conn.modify_s(web.safestr(dn), self.mod_attrs)
            return (True,)
        except Exception, e:
            return (False, ldaputils.getExceptionDesc(e))

    # Search LDAP with display name (cn), email, shadowAddress.
    def search(self, s):
        # Escape search string.
        s = escape_filter_chars(s)

        filter = """(&(|(objectClass=mailDomain)(objectClass=mailUser)(objectClass=mailList)(objectClass=mailAlias))(|(cn=*%s*)(mail=*%s*)(shadowAddress=*%s*)))""" % (s, s, s,)
        searchAttrs = ['objectClass', 'cn', 'mail', 'accountStatus',
                       'domainName',    # Domain.
                       'shadowAddress', 'employeeNumber', 'mailQuota',  # User
                       'accessPolicy',  # Mail list.
                       'createTimestamp',
                      ]

        # Define search dn. Must be a list.
        basedn = []
        if session.get('domainGlobalAdmin') is True:
            # Search whole LDAP server if admin is domainGlobalAdmin.
            basedn += [self.basedn]
        else:
            # Search all domains which under control. 
            # - Get all domains.
            # - Convert domains to list of dn.
            allDomains = self.getAllDomains(attrs=['domainName',],)
            if allDomains[0] is True:
                for d in allDomains[1]:
                    basedn += [d[0]]

        result = []
        msg = {}
        for dn in basedn:
            try:
                res = self.conn.search_s(
                    dn,
                    ldap.SCOPE_SUBTREE,
                    filter,
                    searchAttrs,
                )
                result += res
            except Exception, e:
                #msg[dn] = str(e)
                pass

        if len(result) > 0:
            return (True, result)
        else:
            return (False, [])

    # Get number of current accounts.
    def getNumberOfCurrentAccountsUnderDomain(self, domain, accountType='user', filter=None):
        # accountType in ['user', 'list', 'alias',]
        self.domain = web.safestr(domain)
        self.domaindn = ldaputils.convKeywordToDN(self.domain, accountType='domain')
        
        if filter is not None:
            self.searchdn = self.domaindn
            self.filter = filter
        else:
            if accountType == 'user':
                self.searchdn = attrs.DN_BETWEEN_USER_AND_DOMAIN + self.domaindn
                self.filter = '(&(objectClass=mailUser)(!(mail=@%s)))' % self.domain
            elif accountType == 'maillist':
                self.searchdn = attrs.DN_BETWEEN_MAILLIST_AND_DOMAIN + self.domaindn
                self.filter = '(objectClass=mailList)'
            else:
                self.searchdn = self.domaindn
                self.filter = '(&(objectClass=mailUser)(!(mail=@%s)))' % self.domain

        try:
            result = self.conn.search_s(
                self.searchdn,
                ldap.SCOPE_SUBTREE,
                self.filter,
                ['dn',],
            )
            return (True, len(result))
        except Exception, e:
            return (False, ldaputils.getExceptionDesc(e))

    # Check whether domain name already exist (domainName, domainAliasName).
    def isDomainExists(self, domain):
        # Return True if account is invalid or exist.
        self.domain = web.safestr(domain).strip().lower()

        # Check domain name.
        if not iredutils.isDomain(self.domain):
            # Return True if invalid.
            return True

        # Check domainName and domainAliasName.
        try:
            result = self.conn.search_s(
                cfg.ldap.get('basedn'),
                ldap.SCOPE_ONELEVEL,
                '(|(domainName=%s)(domainAliasName=%s))' % (self.domain, self.domain),
                ['domainName', 'domainAliasName',],
            )
            if len(result) > 0:
                # Domain name exist.
                return True
            else:
                return False
        except Exception, e:
            return True

    # Check whether account exist or not.
    def isAccountExists(self, domain, filter=None,):
        # Return True if account is invalid or exist.
        self.domain = web.safestr(domain)
        if not iredutils.isDomain(self.domain):
            return True

        try:
            self.number = self.getNumberOfCurrentAccountsUnderDomain(
                domain=self.domain,
                filter=filter,
            )

            if self.number[0] is True and self.number[1] == 0:
                # Account not exist.
                return False
            else:
                return True
        except Exception, e:
            # Account 'EXISTS' (fake) if ldap lookup failed.
            return True

    @decorators.require_domain_access
    def enableOrDisableAccount(self, domain, account, dn, action, accountTypeInLogger=None):
        self.domain = web.safestr(domain).strip().lower()
        self.account = web.safestr(account).strip().lower()
        self.dn = escape_filter_chars(web.safestr(dn))

        # Validate operation action.
        if action in ['enable', 'disable',]:
            self.action = action
        else:
            return (False, 'INVALID_ACTION')

        # Set value of valid account status.
        if action == 'enable':
            self.status = attrs.ACCOUNT_STATUS_ACTIVE
        else:
            self.status = attrs.ACCOUNT_STATUS_DISABLED

        try:
            self.updateAttrSingleValue(
                dn=self.dn,
                attr='accountStatus',
                value=self.status,
            )

            if accountTypeInLogger is not None:
                web.logger(
                    msg="%s %s: %s." % (str(action).capitalize(), str(accountTypeInLogger), self.account),
                    domain=self.domain,
                    event=self.action,
                )

            return (True,)
        except ldap.LDAPError, e:
            return (False, ldaputils.getExceptionDesc(e))


    @decorators.require_domain_access
    def deleteObjWithDN(self, domain, dn, account, accountType,):
        self.domain = web.safestr(domain)
        if not iredutils.isDomain(self.domain):
            return (False, 'INVALID_DOMAIN_NAME')

        self.dn = escape_filter_chars(dn)

        # Used for logger.
        self.account = web.safestr(account)
        self.accountType = web.safestr(accountType)

        try:
            deltree.DelTree(self.conn, self.dn, ldap.SCOPE_SUBTREE)
            web.logger(
                msg="Delete %s: %s." % (accountType, self.account),
                domain=self.domain,
                event='delete',
            )

            return (True,)
        except Exception, e:
            return (False, ldaputils.getExceptionDesc(e))


    def getSizelimitFromAccountLists(self, accountList, sizelimit=50, curPage=1, domain=None, accountType=None,):
        # Return a dict which contains:
        #   - totalAccounts: number of total accounts
        #   - accountList: list of accounts used to display in current page
        #   - totalPages: number of total pages show be showed in account list page.
        #   - totalQuota: number of domain quota size. Only available when accountType=='user'.

        # Initial a dict to set default values.
        result = {
            'totalAccounts': 0,         # Integer
            'accountList': [],          # List
            'totalPages': 0,            # Integer
            'totalQuota': 0,            # Integer
            'currentQuota': {},     # Dict
        }

        # Get total accounts.
        totalAccounts = len(accountList)
        result['totalAccounts'] = totalAccounts

        # Get number of actual pages.
        if totalAccounts % sizelimit == 0:
            totalPages = totalAccounts / sizelimit
        else:
            totalPages = (totalAccounts / sizelimit) + 1
        result['totalPages'] = totalPages

        if curPage >= totalPages:
            curPage = totalPages

        # Sort accounts in place.
        if isinstance(accountList, types.ListType):
            accountList.sort()
        else:
            pass

        # Get total domain mailbox quota.
        if accountType == 'user':
            counter = 0
            for i in accountList:
                quota = i[1].get('mailQuota', ['0'])[0]
                if quota.isdigit():
                    result['totalQuota'] += int(quota)
                    counter += 1

            # Update number of current domain quota size in LDAP (@attrs.ATTR_DOMAIN_CURRENT_QUOTA_SIZE).
            if domain is not None:
                # Update number of current domain quota size in LDAP.
                try:
                    dnDomain = ldaputils.convKeywordToDN(domain, accountType='domain')
                    self.updateAttrSingleValue(
                        dn=dnDomain,
                        attr=attrs.ATTR_DOMAIN_CURRENT_QUOTA_SIZE,
                        value=str(result['totalQuota']),
                    )
                except Exception, e:
                    pass

        # Get account list used to display in current page.
        if totalAccounts > sizelimit and totalAccounts < (curPage-1)*sizelimit:
            accountList = accountList[-1:-sizelimit]
        else:
            accountList = accountList[(curPage-1)*sizelimit: (curPage-1)*sizelimit+sizelimit]
        result['accountList'] = accountList

        return result

    @decorators.require_domain_access
    def getDomainCurrentQuotaSizeFromLDAP(self, domain):
        self.domain = web.safestr(domain).strip().lower()
        if not iredutils.isDomain(self.domain):
            return (False, 'INVALID_DOMAIN_NAME')

        self.domainDN = ldaputils.convKeywordToDN(self.domain, accountType='domain')

        # Initial @domainCurrentQuotaSize
        self.domainCurrentQuotaSize = 0

        try:
            # Use '(!(mail=@domain.ltd))' to hide catch-all account.
            self.users = self.conn.search_s(
                attrs.DN_BETWEEN_USER_AND_DOMAIN + self.domainDN,
                ldap.SCOPE_SUBTREE,
                '(&(objectClass=mailUser)(!(mail=@%s)))' % self.domain,
                attrs.USER_SEARCH_ATTRS,
            )

            # Update @domainCurrentUserNumber
            self.updateAttrSingleValue(self.domainDN, 'domainCurrentUserNumber', len(self.users))

            for i in self.users:
                quota = i[1].get('mailQuota', ['0'])[0]
                if quota.isdigit():
                    self.domainCurrentQuotaSize += int(quota)
            return (True, self.domainCurrentQuotaSize)
        except ldap.NO_SUCH_OBJECT:
            #self.conn.add_s(
            #        attrs.DN_BETWEEN_USER_AND_DOMAIN + self.domainDN,
            #        iredldif.ldif_group(attrs.GROUP_USERS),
            #        )
            return (False, 'NO_SUCH_OBJECT')
        except ldap.SIZELIMIT_EXCEEDED:
            return (False, 'EXCEEDED_LDAP_SERVER_SIZELIMIT')
        except Exception, e:
            return (False, ldaputils.getExceptionDesc(e))

    @decorators.require_domain_access
    def getAvailableDomainNames(self, domain):
        '''Get list of domainName and domainAliasName by quering domainName.

        >>> getAvailableDomainNames(domain='example.com')
        (True, ['example.com', 'aliasdomain01.com', 'aliasdomain02.com', ...])
        '''
        self.domain = web.safestr(domain).strip().lower()
        if not iredutils.isDomain(self.domain):
            return (False, 'INVALID_DOMAIN_NAME')

        self.dnOfDomain = ldaputils.convKeywordToDN(self.domain, accountType='domain')

        try:
            result = self.conn.search_s(
                self.dnOfDomain,
                ldap.SCOPE_BASE,
                '(&(objectClass=mailDomain)(domainName=%s))' % self.domain,
                ['domainName', 'domainAliasName'],
            )
            return (True, result[0][1].get('domainName', []) + result[0][1].get('domainAliasName', []))
        except Exception, e:
            return (False, ldaputils.getExceptionDesc(e))

    def getDnWithKeyword(self, value, accountType='user'):
        self.keyword = web.safestr(value)

        if accountType == 'user':
            if attrs.RDN_USER == 'mail':
                if not iredutils.isEmail(self.keyword):
                    return False
                return ldaputils.convKeywordToDN(self.keyword, accountType='user')
            else:
                self.domain = self.keyword.split('@', 1)[-1]
                self.dnOfDomain = ldaputils.convKeywordToDN(self.domain, accountType='domain',)
                try:
                    result = self.conn.search_s(
                        attrs.DN_BETWEEN_USER_AND_DOMAIN + self.dnOfDomain,
                        ldap.SCOPE_SUBTREE,
                        '(&(objectClass=mailUser)(mail=%s))' % (self.keyword),
                    )
                    if len(result) == 1:
                        self.dn = result[0][0]
                        return self.dn
                    else:
                        return False
                except Exception, e:
                    return False
        else:
            # Unsupported accountType.
            return False
